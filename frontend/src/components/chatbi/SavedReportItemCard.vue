<template>
  <div
    class="group/item flex flex-col rounded-lg border border-blue-100/60 dark:border-blue-900/30 bg-white dark:bg-gray-900/40 hover:border-blue-300 dark:hover:border-blue-700/60 overflow-hidden shadow-xs"
  >
    <button
      type="button"
      class="w-full flex flex-col p-2.5 text-left transition-all min-w-0"
      :class="isDisabled ? 'cursor-not-allowed opacity-75 bg-gray-50/60 dark:bg-gray-900/60' : 'active:scale-[0.99] hover:bg-blue-50/30 dark:hover:bg-blue-950/20'"
      :disabled="isDisabled"
      :title="buttonTitle"
      @click="emit('execute', report)"
    >
      <span class="text-xs font-bold text-gray-800 dark:text-gray-200 truncate w-full" :title="report.title">
        {{ report.title }}
      </span>
      <span class="text-[10px] text-gray-400 dark:text-gray-500 truncate mt-1 w-full">
        {{ report.is_owner ? '我的报表' : `来自 ${report.owner_name || '共享用户'}` }}
        <span v-if="report.status === 'error'" class="text-red-500"> · 最近运行失败</span>
        <span v-else-if="report.last_success_at" class="text-emerald-500"> · 已运行</span>
      </span>
      <span class="flex items-center gap-1 mt-1">
        <span
          v-if="!report.is_owner || report.run_permission_status === 'denied'"
          class="inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-bold"
          :class="permissionClass"
          :title="report.run_permission_message || permissionLabel"
        >
          {{ permissionLabel }}
        </span>
        <span
          v-if="report.is_owner && report.share_summary"
          class="inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-bold bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-300"
          :title="shareTargetLabel"
        >
          {{ report.share_summary }}
        </span>
      </span>
      <span v-if="report.original_query" class="text-[10px] text-gray-400 dark:text-gray-500 truncate mt-1 w-full" :title="report.original_query">
        问: {{ report.original_query }}
      </span>
      <span v-if="report.tags?.length" class="flex flex-wrap gap-1 mt-1">
        <span
          v-for="tag in report.tags.slice(0, 3)"
          :key="tag"
          class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-950/30 text-[9px] text-blue-600 dark:text-blue-300"
        >
          {{ tag }}
        </span>
      </span>
      <span class="text-[9px] text-gray-400 dark:text-gray-500 mt-1 select-none font-mono">
        {{ formattedDate }}
      </span>
    </button>
    <div class="flex items-center justify-end px-2.5 py-1 bg-gray-50/50 dark:bg-gray-900/10 border-t border-blue-50/30 dark:border-blue-950/20 text-gray-400 gap-1.5">
      <button
        v-if="report.is_owner && report.subscription_status"
        type="button"
        class="mr-auto inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[9px] font-bold transition-colors"
        :class="subscriptionClass"
        :title="subscriptionTitle"
        @click.stop="emit('subscription', report)"
      >
        <span class="h-1.5 w-1.5 rounded-full bg-current" />
        {{ subscriptionLabel }}
      </button>
      <button
        v-if="report.is_owner"
        type="button"
        class="flex items-center justify-center p-1 rounded hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/40 transition-all duration-200 cursor-pointer"
        title="编辑暂存"
        @click.stop="emit('edit', report)"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
      <button
        type="button"
        class="flex items-center justify-center p-1 rounded hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 transition-all duration-200 cursor-pointer"
        title="报表详情"
        @click.stop="emit('detail', report)"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M12 3a9 9 0 110 18 9 9 0 010-18z" />
        </svg>
      </button>
      <button
        type="button"
        class="flex items-center justify-center p-1 rounded transition-all duration-200 cursor-pointer"
        :class="report.is_favorite ? 'text-amber-500 hover:bg-amber-50 dark:hover:bg-amber-950/30' : 'text-gray-400 hover:text-amber-500 hover:bg-amber-50 dark:hover:bg-amber-950/30'"
        title="收藏"
        @click.stop="emit('favorite', report)"
      >
        <svg class="w-3.5 h-3.5" :fill="report.is_favorite ? 'currentColor' : 'none'" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.48 3.5l2.25 4.56 5.03.73-3.64 3.55.86 5.01-4.5-2.37-4.5 2.37.86-5.01L4.2 8.79l5.03-.73 2.25-4.56z" />
        </svg>
      </button>
      <button
        type="button"
        class="flex items-center justify-center p-1 rounded transition-all duration-200 cursor-pointer"
        :class="report.pinned_at ? 'text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/40' : 'text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/40'"
        title="置顶"
        @click.stop="emit('pin', report)"
      >
        <svg class="w-3.5 h-3.5" :fill="report.pinned_at ? 'currentColor' : 'none'" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 4l6 6-4 1-5 5-1 4-6-6 4-1 5-5 1-4z" />
        </svg>
      </button>
      <button
        v-if="report.is_owner"
        type="button"
        class="flex items-center justify-center p-1 rounded hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 transition-all duration-200 cursor-pointer"
        title="共享报表"
        @click.stop="emit('share', report)"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 8a3 3 0 10-2.83-4H12a3 3 0 003 4zm0 8a3 3 0 10-2.83 4H12a3 3 0 003-4zM6 13a3 3 0 100-6 3 3 0 000 6zm2.59-2.51l4.82 2.02M13.41 5.49L8.59 7.51" />
        </svg>
      </button>
      <button
        v-else
        type="button"
        class="flex items-center justify-center p-1 rounded transition-all duration-200"
        :class="isDisabled ? 'text-gray-300 dark:text-gray-600 bg-gray-50/70 dark:bg-gray-900/60 cursor-not-allowed opacity-30' : 'text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 cursor-pointer'"
        :disabled="isDisabled"
        :title="copyTitle"
        @click.stop="emit('copy', report)"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 8h10a2 2 0 012 2v8a2 2 0 01-2 2H8a2 2 0 01-2-2V10a2 2 0 012-2zm-2 8H5a2 2 0 01-2-2V5a2 2 0 012-2h9a2 2 0 012 2v1" />
        </svg>
      </button>
      <button
        v-if="report.is_owner"
        type="button"
        class="flex items-center justify-center p-1 rounded hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/40 transition-all duration-200 cursor-pointer"
        title="删除暂存"
        @click.stop="emit('delete', report)"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  report: any;
  formatDate: (iso?: string | null) => string;
}>();

const emit = defineEmits<{
  (event: "execute", report: any): void;
  (event: "edit", report: any): void;
  (event: "detail", report: any): void;
  (event: "favorite", report: any): void;
  (event: "pin", report: any): void;
  (event: "share", report: any): void;
  (event: "copy", report: any): void;
  (event: "delete", report: any): void;
  (event: "subscription", report: any): void;
}>();

const isDisabled = computed(() => props.report.run_permission_status === "denied");

const permissionLabel = computed(() => {
  if (props.report.run_permission_status === "denied") return "无数据权限";
  if (props.report.run_permission_status === "allowed") return "可运行";
  return "待确认";
});

const permissionClass = computed(() => {
  if (props.report.run_permission_status === "denied") {
    return "bg-red-50 dark:bg-red-950/30 text-red-600 dark:text-red-300";
  }
  if (props.report.run_permission_status === "allowed") {
    return "bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-300";
  }
  return "bg-amber-50 dark:bg-amber-950/30 text-amber-600 dark:text-amber-300";
});

const shareTargetLabel = computed(() => {
  const targets = Array.isArray(props.report.share_targets) ? props.report.share_targets : [];
  if (!targets.length) return "未共享";
  const labels = targets.map((target: any) => {
    const prefix = target.target_type === "role" ? "角色" : "用户";
    return `${prefix}：${target.target_name || `ID ${target.target_id}`}`;
  });
  return `已共享给 ${labels.join("、")}`;
});

const buttonTitle = computed(() => {
  if (props.report.run_permission_status === "denied") {
    return props.report.run_permission_message || "暂无该报表所需数据权限，无法运行。";
  }
  if (props.report.is_owner && props.report.share_summary) {
    return shareTargetLabel.value;
  }
  return props.report.title || "运行黄金报表";
});

const copyTitle = computed(() => {
  if (isDisabled.value) {
    return props.report.run_permission_message || "暂无该报表所需数据权限，无法复制。";
  }
  return "复制为我的报表";
});

const formattedDate = computed(() => props.formatDate(props.report.created_at));

const subscriptionLabel = computed(() => {
  if (props.report.subscription_status === "active") return "已订阅";
  if (props.report.subscription_status === "paused") return "已暂停";
  return "订阅异常";
});

const subscriptionClass = computed(() => {
  if (props.report.subscription_status === "active") return "bg-emerald-50 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-950/30 dark:text-emerald-300";
  if (props.report.subscription_status === "paused") return "bg-gray-100 text-gray-500 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300";
  return "bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-950/30 dark:text-red-300";
});

const subscriptionScheduleLabel = computed(() => {
  const parts = String(props.report.subscription_cron_expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return props.report.subscription_cron_expr || "";
  const [minute, hour, monthday, month, weekday] = parts;
  if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour)) return props.report.subscription_cron_expr;
  const time = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  if (month === "*" && weekday === "*" && monthday === "*") return `每天 ${time}`;
  if (month === "*" && weekday === "*" && /^\d+$/.test(monthday)) return `每月${monthday}日 ${time}`;
  if (month === "*" && monthday === "*" && /^[0-6]$/.test(weekday)) return `每周${["日", "一", "二", "三", "四", "五", "六"][Number(weekday)]} ${time}`;
  return props.report.subscription_cron_expr;
});

const subscriptionTitle = computed(() => {
  const schedule = subscriptionScheduleLabel.value ? `运行周期：${subscriptionScheduleLabel.value}` : "";
  const nextRun = props.report.subscription_next_run_at ? `下次运行：${props.formatDate(props.report.subscription_next_run_at)}` : "";
  return [subscriptionLabel.value, schedule, nextRun, "点击管理订阅"].filter(Boolean).join("\n");
});
</script>
