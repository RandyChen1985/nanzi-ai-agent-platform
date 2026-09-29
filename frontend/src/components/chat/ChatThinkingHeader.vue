<template>
  <div
    role="button"
    tabindex="0"
    class="group/header flex w-full select-none cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-gray-100 sm:gap-2 sm:px-3 sm:py-2"
    :class="{
      'border border-transparent hover:border-gray-200': bordered,
      'dark:hover:bg-gray-700/50': darkMode,
    }"
    @click="expanded = !expanded"
    @keydown.enter.prevent="expanded = !expanded"
    @keydown.space.prevent="expanded = !expanded"
  >
    <div class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded bg-gray-100 text-gray-500" :class="{ 'dark:bg-gray-700': darkMode }">
      <span v-if="isThinking" class="thought-status-dot" aria-label="进行中" title="进行中" />
      <svg v-else class="h-3.5 w-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
      </svg>
    </div>

    <div class="flex min-w-0 items-center gap-1.5 overflow-hidden sm:gap-2" :class="{ 'flex-1': expanded }">
      <span class="truncate text-xs font-semibold text-gray-700" :class="{ 'dark:text-gray-300': darkMode }">{{ title }}</span>
      <button
        v-if="showCopy"
        type="button"
        class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-400 opacity-70 transition-all hover:bg-gray-200/80 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/60 dark:hover:text-gray-200"
        :class="{ '!text-emerald-500 !opacity-100 dark:!text-emerald-400': isCopied }"
        :title="isCopied ? '已复制' : '复制思考内容'"
        @click.stop="emit('copy')"
      >
        <svg v-if="isCopied" class="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
        </svg>
        <svg v-else class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
        </svg>
      </button>
      <span
        v-if="stepCount > 0"
        class="flex-shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500"
        :class="{ 'dark:bg-gray-700': darkMode }"
        :title="stepBadgeTitle"
      >
        <span class="sm:hidden">{{ stepCount }}</span>
        <span class="hidden sm:inline">
          {{ stepCount }} 步骤<template v-if="hiddenStepCount > 0"> · 已折叠 {{ hiddenStepCount }}</template>
        </span>
      </span>
      <span
        v-if="duration"
        class="flex-shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500"
        :class="{ 'dark:bg-gray-700': darkMode }"
        :title="'总耗时 ' + duration + 's'"
      >
        耗时 {{ duration }}s
      </span>
      <span
        v-if="actionSummary"
        class="hidden sm:inline-flex items-center text-[10px] text-gray-500 dark:text-gray-400 font-normal truncate max-w-[18rem]"
        :title="actionSummary"
      >
        · {{ actionSummary }}
      </span>
      <span
        v-if="skillSummary"
        class="hidden max-w-[9rem] shrink-0 truncate rounded-full border border-purple-100 bg-purple-50 px-1.5 py-0.5 text-[10px] font-semibold text-purple-600 sm:inline-flex sm:items-center sm:gap-0.5"
        :class="{ 'dark:border-purple-900/30 dark:bg-purple-950/40 dark:text-purple-400': darkMode }"
        :title="skillSummary"
      >
        ⚡ {{ skillSummary }}
      </span>
      <span
        v-if="currentStep && !expanded"
        class="min-w-0 flex-1 truncate text-[10px] font-normal text-gray-400"
        :title="currentStep"
      >
        {{ currentStep }}
      </span>
    </div>

    <div
      class="flex shrink-0 items-center gap-1.5 text-gray-400"
      :class="expanded ? 'ml-auto sm:gap-2' : 'gap-1.5'"
    >
      <svg
        class="h-4 w-4 shrink-0 transform transition-transform duration-200"
        :class="{ 'rotate-180': expanded }"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
      </svg>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{
  isThinking?: boolean;
  title: string;
  stepCount?: number;
  hiddenStepCount?: number;
  skillSummary?: string;
  actionSummary?: string;
  currentStep?: string;
  duration?: string;
  bordered?: boolean;
  darkMode?: boolean;
  showCopy?: boolean;
  isCopied?: boolean;
}>(), {
  isThinking: false,
  stepCount: 0,
  hiddenStepCount: 0,
  skillSummary: "",
  actionSummary: "",
  currentStep: "",
  duration: "",
  bordered: false,
  darkMode: false,
  showCopy: false,
  isCopied: false,
});

const emit = defineEmits<{
  (e: "copy"): void;
}>();

const expanded = defineModel<boolean>("expanded", { default: false });

const stepBadgeTitle = computed(() => {
  if (!props.stepCount) return "";
  return props.hiddenStepCount > 0
    ? `${props.stepCount} 步骤 · 已折叠 ${props.hiddenStepCount}`
    : `${props.stepCount} 步骤`;
});
</script>

<style scoped>
.thought-status-dot {
  display: inline-block;
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 9999px;
  background: #0ea5e9;
  box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.35);
  animation: thought-header-breathe 1.6s ease-in-out infinite;
}

@keyframes thought-header-breathe {
  0%, 100% { opacity: 0.55; transform: scale(0.85); box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.28); }
  50% { opacity: 1; transform: scale(1.12); box-shadow: 0 0 0 0.28rem rgba(14, 165, 233, 0.08); }
}

@media (prefers-reduced-motion: reduce) {
  .thought-status-dot {
    animation: none;
  }
}
</style>
