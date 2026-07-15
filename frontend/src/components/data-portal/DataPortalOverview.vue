<template>
  <section class="space-y-5">
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <button class="portal-stat-card text-left" type="button" @click="openFailure">
        <span class="portal-stat-label">运行失败</span>
        <strong class="portal-stat-value" :class="attention.failed_runs_today ? 'text-red-500' : ''">{{ attention.failed_runs_today }}</strong>
        <span class="portal-stat-detail">{{ failureDetail }}</span>
      </button>
      <div class="portal-stat-card">
        <span class="portal-stat-label">今日新简报</span>
        <strong class="portal-stat-value">{{ attention.digests_today }}</strong>
        <span class="portal-stat-detail">{{ digestDetail }}</span>
      </div>
      <div class="portal-stat-card">
        <span class="portal-stat-label">订阅运行正常</span>
        <strong class="portal-stat-value">{{ attention.active_subscriptions }}</strong>
        <span class="portal-stat-detail">今日已完成 {{ attention.completed_subscriptions_today }} 个</span>
      </div>
    </div>

    <div>
      <div class="mb-2 flex items-center justify-between">
        <h2 class="text-base font-bold text-gray-900 dark:text-gray-100">最近分析</h2>
        <span class="text-xs text-gray-400">报表运行与智能简报</span>
      </div>
      <div v-if="activities.length" class="divide-y divide-gray-100 border-y border-gray-100 dark:divide-gray-800 dark:border-gray-800">
        <button
          v-for="activity in activities"
          :key="`${activity.type}-${activity.id}`"
          type="button"
          class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-1 py-3 text-left transition hover:bg-gray-50 dark:hover:bg-gray-800/40 sm:grid-cols-[minmax(0,1fr)_120px_100px]"
          @click="emit('open-activity', activity)"
        >
          <span class="min-w-0">
            <strong class="block truncate text-sm text-gray-900 dark:text-gray-100">{{ activity.title }}</strong>
            <span class="block truncate text-xs text-gray-500 dark:text-gray-400">{{ activity.subtitle }}</span>
          </span>
          <span class="hidden text-xs text-gray-400 sm:block">{{ formatTime(activity.occurred_at) }}</span>
          <span class="rounded-lg bg-blue-50 px-2 py-1 text-center text-xs font-medium text-blue-600 dark:bg-blue-950/40 dark:text-blue-300">{{ activityLabel(activity) }}</span>
        </button>
      </div>
      <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400 dark:border-gray-800">
        还没有最近分析记录，可从推荐场景开始一次 ChatBI 查询。
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { DataPortalActivity, DataPortalAttention } from "@/types/dataPortal";

const props = defineProps<{ attention: DataPortalAttention; activities: DataPortalActivity[] }>();
const emit = defineEmits<{ (event: "open-activity", value: DataPortalActivity | Record<string, any>): void }>();

const failureDetail = computed(() => props.attention.latest_failed_run?.title || "今日没有失败运行");
const digestDetail = computed(() => props.attention.latest_digest_at ? `最近生成 ${formatTime(props.attention.latest_digest_at)}` : "今日还没有新简报");
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "--";
const activityLabel = (activity: DataPortalActivity) => activity.type === "digest" ? "查看简报" : activity.status === "failed" ? "查看失败" : "打开报表";
const openFailure = () => {
  if (props.attention.latest_failed_run) emit("open-activity", props.attention.latest_failed_run);
};
</script>

<style scoped>
.portal-stat-card { @apply flex min-h-[112px] flex-col rounded-2xl border border-gray-100 bg-white p-4 shadow-sm transition hover:border-blue-200 dark:border-gray-800 dark:bg-gray-900; }
.portal-stat-label { @apply text-xs text-gray-500 dark:text-gray-400; }
.portal-stat-value { @apply mt-2 text-2xl font-bold text-gray-900 dark:text-gray-100; }
.portal-stat-detail { @apply mt-auto truncate text-xs text-gray-400; }
</style>
