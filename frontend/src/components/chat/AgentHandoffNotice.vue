<template>
  <aside class="mb-2.5 rounded-xl border border-sky-100 bg-sky-50/75 px-3 py-2.5 text-xs dark:border-primary-dark/60 dark:bg-primary-dark/25" role="status" aria-label="智能体转交提示">
    <div class="flex items-center gap-2 font-semibold text-primary-dark dark:text-sky-300">
      <span class="flex h-5 w-5 items-center justify-center rounded-full bg-sky-100 text-[11px] dark:bg-primary-dark/60">↗</span>
      <span>已转交给「{{ handoff.to_display_name }}」继续处理</span>
    </div>
    <div class="mt-1 pl-7 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
      由「{{ handoff.from_display_name }}」完成请求分流
    </div>
  </aside>
</template>

<script setup lang="ts">
import type { AgentHandoffNoticeData } from "@/types/agentHandoff";

defineProps<{ handoff: AgentHandoffNoticeData }>();
</script>
