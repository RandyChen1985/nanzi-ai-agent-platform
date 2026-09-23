<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  /** 工具入参展示文本；命令类工具（Bash）即命令原文。 */
  text: string;
  /** 工具名或卡片标题，用于判断该展示为「命令」还是通用「参数」。 */
  toolName?: string;
  /** 复制按钮的 key，沿用父级 handleCopy 的约定。 */
  copyKey: string;
  copiedKey: string | null;
}>();

const emit = defineEmits<{ copy: [key: string, text: string] }>();

const COMMAND_TOOL_NAMES = ["bash", "exec_command", "shell", "run_command"];

// 这里做子串匹配（后端 stream_reconcile 用工具名精确匹配）：本组件拿到的
// toolName 可能是 tool_name，也可能是形如「工具完成: Bash (1200ms)」的卡片标题，
// 标题场景下只有子串匹配才能识别成命令。
const isCommandTool = computed(() => {
  const name = String(props.toolName || "").toLowerCase();
  return COMMAND_TOOL_NAMES.some((tool) => name.includes(tool));
});

const label = computed(() => (isCommandTool.value ? "命令" : "参数"));
</script>

<template>
  <div class="mb-1 rounded border border-gray-200/70 bg-gray-50/70 p-1.5 dark:border-gray-700/70 dark:bg-gray-800/40">
    <div class="flex items-center justify-between gap-2">
      <span class="text-[10px] font-medium text-gray-400 dark:text-gray-500">{{ label }}</span>
      <button
        type="button"
        class="flex h-4 w-4 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200"
        :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === copyKey }"
        :title="copiedKey === copyKey ? '已复制' : '复制'"
        :aria-label="copiedKey === copyKey ? '已复制' : '复制' + label"
        @click.stop="emit('copy', copyKey, text)"
      >
        <svg v-if="copiedKey === copyKey" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
        </svg>
        <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
        </svg>
      </button>
    </div>
    <pre class="mt-0.5 whitespace-pre-wrap break-words pr-1 font-mono text-[10px] leading-relaxed text-gray-600 dark:text-gray-300">{{ text }}</pre>
  </div>
</template>
