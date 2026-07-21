<template>
  <button
    type="button"
    class="flex w-full select-none items-center space-x-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-gray-100"
    :class="{
      'border border-transparent hover:border-gray-200': bordered,
      'dark:hover:bg-gray-700/50': darkMode,
    }"
    @click="expanded = !expanded"
  >
    <div class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded bg-tdSecondaryContainer dark:bg-tdSecondaryContainer-dark text-[var(--primary-color)]" :class="{ 'dark:text-blue-400': darkMode }">
      <svg v-if="isThinking" class="h-3.5 w-3.5 animate-spin text-[var(--primary-color)]" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
      </svg>
      <svg v-else class="h-3.5 w-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
      </svg>
    </div>

    <div class="flex min-w-0 flex-1 items-center justify-between">
      <div class="flex items-center space-x-2 overflow-hidden">
        <span class="truncate text-xs font-semibold text-gray-700" :class="{ 'dark:text-gray-300': darkMode }">{{ title }}</span>
        <span v-if="stepCount > 0" class="rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500" :class="{ 'dark:bg-gray-700': darkMode }">
          {{ stepCount }} 步骤<template v-if="hiddenStepCount > 0"> · 已折叠 {{ hiddenStepCount }}</template>
        </span>
        <span v-if="skillSummary" class="flex items-center gap-0.5 rounded-full border border-purple-100 bg-purple-50 px-1.5 py-0.5 text-[10px] font-semibold text-purple-600" :class="{ 'dark:border-purple-900/30 dark:bg-purple-950/40 dark:text-purple-400': darkMode }">
          ⚡ {{ skillSummary }}
        </span>
      </div>
      <span class="ml-2 flex-shrink-0 font-mono text-[10px] text-gray-400">{{ duration ? `${duration}s` : "" }}</span>
    </div>

    <svg
      class="h-4 w-4 transform text-gray-400 transition-transform duration-200"
      :class="{ 'rotate-180': expanded }"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
    </svg>
  </button>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  isThinking?: boolean;
  title: string;
  stepCount?: number;
  hiddenStepCount?: number;
  skillSummary?: string;
  duration?: string;
  bordered?: boolean;
  darkMode?: boolean;
}>(), {
  isThinking: false,
  stepCount: 0,
  hiddenStepCount: 0,
  skillSummary: "",
  duration: "",
  bordered: false,
  darkMode: false,
});

const expanded = defineModel<boolean>("expanded", { default: false });
</script>
