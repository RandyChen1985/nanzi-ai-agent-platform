<script setup lang="ts">
import { computed, ref } from "vue";

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

// 入参区块自带折叠开关：长命令、长 SQL 只占一行标题，不必随所属时间线行一起被
// 收起（那一行可能还要看错误原因、模型元信息和工具输出）。默认展开以保持原有视觉。
const bodyExpanded = ref(true);
</script>

<template>
  <!-- 无框内容块：盒子（边框 / 底色 / 宽度）由父级容器提供，元信息也归父级容器，
       这样「参数 + 模型 / 温度 / 工具状态」是一整块，而不是框内框外两截。 -->
  <div>
    <div class="flex items-center justify-between gap-2">
      <button
        type="button"
        class="flex min-w-0 items-center gap-1 rounded text-[10px] font-medium text-gray-400 transition-colors hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
        :aria-expanded="bodyExpanded"
        :title="bodyExpanded ? `收起${label}` : `展开${label}`"
        @click="bodyExpanded = !bodyExpanded"
      >
        <svg class="h-3 w-3 shrink-0 transition-transform" :class="{ 'rotate-180': bodyExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
        </svg>
        <span>{{ label }}</span>
      </button>
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
    <pre v-show="bodyExpanded" class="mt-0.5 whitespace-pre-wrap break-words pr-1 font-mono text-[10px] leading-relaxed text-gray-600 dark:text-gray-300">{{ text }}</pre>
  </div>
</template>
