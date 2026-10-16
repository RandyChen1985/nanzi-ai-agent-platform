<template>
  <transition
    enter-active-class="transition-all duration-200 ease-out"
    enter-from-class="opacity-0 -translate-y-1"
    enter-to-class="opacity-100 translate-y-0"
    leave-active-class="transition-all duration-200 ease-in"
    leave-from-class="opacity-100 translate-y-0"
    leave-to-class="opacity-0 -translate-y-1"
  >
    <div
      v-if="state.phase !== 'hidden'"
      role="status"
      aria-live="polite"
      class="flex items-start gap-2 rounded-2xl border border-slate-200/80 bg-slate-50/90 px-3.5 py-2 text-[12px] leading-5 shadow-xs backdrop-blur-xs dark:border-slate-800/80 dark:bg-slate-900/90"
    >
      <span class="mt-0.5 shrink-0" aria-hidden="true">
        <svg
          v-if="state.phase === 'syncing'"
          class="h-3.5 w-3.5 animate-spin text-blue-500"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <svg
          v-else-if="state.phase === 'done'"
          class="h-3.5 w-3.5 text-emerald-500"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M20 6L9 17l-5-5" />
        </svg>
      </span>

      <div class="min-w-0 flex-1">
        <p class="font-semibold text-slate-800 dark:text-slate-100">{{ state.message }}</p>
        <p
          v-if="state.detail"
          class="mt-0.5 truncate text-[11px] font-normal text-slate-500 dark:text-slate-400"
        >
          {{ state.detail }}
        </p>
      </div>

      <button
        type="button"
        class="shrink-0 rounded px-1 text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
        title="关闭提示"
        aria-label="关闭提示"
        @click="emit('dismiss')"
      >
        ×
      </button>
    </div>
  </transition>
</template>

<script setup lang="ts">
import type { RecoveryCardState } from "@/utils/recoveryCard";

defineProps<{ state: RecoveryCardState }>();
const emit = defineEmits<{ (event: "dismiss"): void }>();
</script>
