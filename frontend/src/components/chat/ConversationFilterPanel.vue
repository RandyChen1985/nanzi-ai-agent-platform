<script setup lang="ts">
/**
 * 会话筛选面板（来源 / 智能体 / 执行状态 / 时间范围）。
 *
 * 聊天页右侧历史面板与用户模式侧栏的「会话列表」共用这一份：
 * 以前这段是内联在 ChatHistorySidebar 里的，侧栏要加同样的筛选时只能复制，
 * 两边条件一旦不同步，用户就得学两套规则。
 *
 * 只负责「面板内容」，卡片外壳（边框 / 阴影 / 浮层定位）由宿主决定 ——
 * 历史面板是侧栏内浮层，用户模式侧栏是从右缘滑出的浮层，定位方式不一样。
 */
import type { AgentOption, ChatHistoryFilters } from "@/composables/chat/useHistoryFilters";

const props = withDefaults(
  defineProps<{
    filters: ChatHistoryFilters;
    agents?: AgentOption[];
    /** 锁定了智能体（例如从某个智能体的历史进入）时隐藏该组：切了也不会生效 */
    hideAgent?: boolean;
  }>(),
  { agents: () => [], hideAgent: false },
);

const emit = defineEmits<{
  (e: "update:filters", value: ChatHistoryFilters): void;
  (e: "reset"): void;
}>();

const scopeOptions: Array<{ value: ChatHistoryFilters["scope"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "task", label: "任务会话" },
  { value: "chat", label: "普通对话" },
];

const statusOptions: Array<{ value: ChatHistoryFilters["status"]; label: string }> = [
  { value: "", label: "全部" },
  { value: "success", label: "成功" },
  { value: "failed", label: "失败" },
];

const timeOptions: Array<{ value: ChatHistoryFilters["timeRange"]; label: string }> = [
  { value: "", label: "全部" },
  { value: "today", label: "今天" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "custom", label: "自定义" },
];

const patch = (part: Partial<ChatHistoryFilters>) => {
  emit("update:filters", { ...props.filters, ...part });
};
</script>

<template>
  <div class="space-y-3">
    <div>
      <div class="mb-1.5 text-[10px] font-bold text-gray-400">会话来源</div>
      <div class="grid grid-cols-3 gap-1">
        <button
          v-for="opt in scopeOptions"
          :key="opt.value"
          type="button"
          class="rounded-lg border px-2 py-1 text-[11px] font-semibold transition-colors"
          :class="
            filters.scope === opt.value
              ? 'border-primary/30 bg-primary/10 text-primary'
              : 'border-transparent text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
          "
          @click="patch({ scope: opt.value })"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <div v-if="!hideAgent">
      <div class="mb-1.5 text-[10px] font-bold text-gray-400">智能体</div>
      <select
        :value="filters.agentId"
        class="w-full rounded-lg border border-gray-200/80 bg-gray-50/80 px-2 py-1 text-[11px] text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/20 dark:border-gray-700/80 dark:bg-gray-800/80 dark:text-gray-200"
        @change="patch({ agentId: ($event.target as HTMLSelectElement).value })"
      >
        <option value="">全部智能体</option>
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">
          {{ agent.display_name }}
        </option>
      </select>
    </div>

    <div>
      <div class="mb-1.5 text-[10px] font-bold text-gray-400">执行状态</div>
      <div class="grid grid-cols-3 gap-1">
        <button
          v-for="opt in statusOptions"
          :key="opt.value"
          type="button"
          class="rounded-lg border px-2 py-1 text-[11px] font-semibold transition-colors"
          :class="
            filters.status === opt.value
              ? 'border-primary/30 bg-primary/10 text-primary'
              : 'border-transparent text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
          "
          @click="patch({ status: opt.value })"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <div>
      <div class="mb-1.5 text-[10px] font-bold text-gray-400">时间范围</div>
      <div class="grid grid-cols-3 gap-1">
        <button
          v-for="opt in timeOptions"
          :key="opt.value"
          type="button"
          class="rounded-lg border px-2 py-1 text-[11px] font-semibold transition-colors"
          :class="
            filters.timeRange === opt.value
              ? 'border-primary/30 bg-primary/10 text-primary'
              : 'border-transparent text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
          "
          @click="patch({ timeRange: opt.value })"
        >
          {{ opt.label }}
        </button>
      </div>
      <div v-if="filters.timeRange === 'custom'" class="mt-2 grid grid-cols-2 gap-1.5">
        <input
          type="date"
          :value="filters.startDate"
          class="rounded-lg border border-gray-200/80 bg-gray-50/80 px-2 py-1 text-[11px] text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/20 dark:border-gray-700/80 dark:bg-gray-800/80 dark:text-gray-200"
          @change="patch({ startDate: ($event.target as HTMLInputElement).value })"
        />
        <input
          type="date"
          :value="filters.endDate"
          class="rounded-lg border border-gray-200/80 bg-gray-50/80 px-2 py-1 text-[11px] text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/20 dark:border-gray-700/80 dark:bg-gray-800/80 dark:text-gray-200"
          @change="patch({ endDate: ($event.target as HTMLInputElement).value })"
        />
      </div>
    </div>

    <div class="flex justify-end border-t border-gray-100 pt-1 dark:border-gray-800">
      <button
        type="button"
        class="rounded-lg px-2.5 py-1 text-[11px] font-semibold text-gray-500 transition-colors hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        @click="emit('reset')"
      >
        重置筛选
      </button>
    </div>
  </div>
</template>
