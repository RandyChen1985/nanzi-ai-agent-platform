<template>
  <section
    v-if="views.length"
    class="grid grid-cols-1 gap-2 sm:grid-cols-3"
    aria-label="固化报表快捷视图"
  >
    <article
      v-for="view in views"
      :key="view.key"
      class="min-w-0 rounded-xl border border-gray-100 bg-gray-50/70 p-3 dark:border-gray-800 dark:bg-gray-900/50"
    >
      <div class="flex items-center justify-between gap-2">
        <div class="flex min-w-0 items-center gap-1.5">
          <span class="text-sm" aria-hidden="true">{{ view.icon }}</span>
          <h3 class="min-w-0 flex-1 truncate text-xs font-bold text-gray-700 dark:text-gray-200">{{ view.label }}</h3>
          <span class="shrink-0 rounded-full bg-white px-1.5 py-0.5 text-[10px] font-bold text-primary shadow-sm dark:bg-gray-800 dark:text-primary-hover">
            {{ view.items.length }}
          </span>
        </div>
      </div>
      <div class="mt-2 flex items-center justify-between gap-2">
        <p v-if="view.items[0]" class="min-w-0 flex-1 truncate text-[11px] font-bold text-gray-700 dark:text-gray-200" :title="view.items[0].title">
          {{ view.items[0].title }}
        </p>
        <button
          v-if="view.items[0]"
          type="button"
          class="min-h-9 shrink-0 rounded-lg border border-primary/20 bg-primary/5 px-3 text-[11px] font-bold text-primary-dark transition hover:border-primary/25 hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-primary/30 dark:border-primary-dark/60 dark:bg-primary-dark/40 dark:text-primary-hover dark:hover:bg-primary-dark/60"
          :aria-label="`运行${view.items[0].title || '报表'}`"
          @click="emit('select', view.items[0])"
        >
          运行
        </button>
      </div>
      <p v-if="view.items[0]" class="mt-1 truncate text-[10px] text-gray-400 dark:text-gray-500">
        {{ view.key === 'frequent' ? `运行 ${Number(view.items[0].user_run_count || 0)} 次` : formatDate(view.items[0].last_success_at || view.items[0].user_last_run_at) || '尚未运行' }}
      </p>
      <p v-else class="mt-2 text-[10px] text-gray-400 dark:text-gray-500">暂无符合条件的报表</p>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  reports: any[];
  formatDate: (iso?: string | null) => string;
}>();

const emit = defineEmits<{
  (event: "select", report: any): void;
}>();

const views = computed(() => {
  const reports = Array.isArray(props.reports) ? props.reports : [];
  const recent = reports
    .filter((report) => report.user_last_run_at || report.last_success_at)
    .slice()
    .sort((a, b) => String(b.user_last_run_at || b.last_success_at).localeCompare(String(a.user_last_run_at || a.last_success_at)))
    .slice(0, 6);
  const frequent = reports
    .filter((report) => Number(report.user_run_count || 0) > 0)
    .slice()
    .sort((a, b) => Number(b.user_run_count || 0) - Number(a.user_run_count || 0))
    .slice(0, 6);
  const subscribed = reports.filter((report) => !!report.subscription_status).slice(0, 6);
  return [
    { key: "recent", label: "最近运行", icon: "🕒", items: recent },
    { key: "frequent", label: "常用报表", icon: "🔥", items: frequent },
    { key: "subscribed", label: "订阅中", icon: "🔔", items: subscribed },
  ];
});
</script>
