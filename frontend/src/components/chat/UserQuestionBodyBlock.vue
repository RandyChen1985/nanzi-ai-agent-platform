<script setup lang="ts">
/**
 * 提问轮正文里的一个带标签小节（「任务进度」/「待你确认」）。
 *
 * 提问轮补全的正文模板是「任务进度：…\n待你确认：…」，直接当普通正文渲染时，
 * 它会和展开的提问卡整段重复，读起来也分不清哪段是进度、哪段是问题。这里把两段
 * 各自包成一个带彩色标签的小节：**默认展开**（进度原文是用户判断「模型到底做完
 * 了什么」的依据，不该一进来就藏起来），点标题行可收起。
 */
const props = defineProps<{
  /** 小节标签，例如「任务进度」「待你确认」。 */
  label: string;
  /** 小节正文（提问卡字段原文，不做截断）。 */
  text: string;
  /** progress = 任务进度（蓝），pending = 待你确认（琥珀）。 */
  tone?: "progress" | "pending";
  /** 收起态由调用方持有：重建消息对象时回到默认的展开。 */
  collapsed?: boolean;
}>();

const emit = defineEmits<{
  (event: "toggle"): void;
}>();

function toggle() {
  emit("toggle");
}
</script>

<template>
  <div
    class="mb-1.5 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem] rounded-lg border"
    :class="
      props.tone === 'pending'
        ? 'border-amber-200/80 bg-amber-50/50 dark:border-amber-900/50 dark:bg-amber-950/20'
        : 'border-sky-200/80 bg-sky-50/50 dark:border-primary-dark/50 dark:bg-primary-dark/20'
    "
    :data-question-body-block="props.tone === 'pending' ? 'pending' : 'progress'"
  >
    <div
      class="flex cursor-pointer select-none items-center gap-2 px-2.5 py-1.5"
      role="button"
      :aria-expanded="!props.collapsed"
      :title="props.collapsed ? '展开' : '收起'"
      @click="toggle"
    >
      <span
        class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold leading-none"
        :class="
          props.tone === 'pending'
            ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300'
            : 'bg-sky-100 text-primary-dark dark:bg-primary-dark/50 dark:text-sky-300'
        "
      >{{ props.label }}</span>
      <svg
        class="ml-auto h-3 w-3 shrink-0 text-gray-400 transition-transform"
        :class="{ 'rotate-180': !props.collapsed }"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
      </svg>
    </div>
    <div
      v-if="!props.collapsed"
      class="whitespace-pre-wrap break-words px-2.5 pb-2 text-[12.5px] leading-relaxed text-gray-700 dark:text-gray-200"
    >{{ props.text }}</div>
  </div>
</template>
