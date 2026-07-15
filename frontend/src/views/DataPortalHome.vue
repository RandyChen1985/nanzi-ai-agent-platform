<template>
  <div :class="props.embedded ? 'bg-white dark:bg-gray-900' : 'min-h-full bg-gray-50/70 pb-20 dark:bg-gray-950 md:pb-0'">
    <div class="grid grid-cols-1 overflow-hidden bg-white dark:bg-gray-900 md:grid-cols-[190px_minmax(0,1fr)]" :class="props.embedded ? 'min-h-[620px]' : 'min-h-[calc(100vh-4rem)]'">
      <aside class="hidden border-r border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-900 md:block">
        <div class="mb-5 flex items-center gap-2 px-2 text-sm font-bold text-gray-900 dark:text-gray-100"><span class="grid h-8 w-8 place-items-center rounded-xl bg-blue-600 text-white">▦</span>我的数据门户</div>
        <nav class="space-y-1">
          <button v-for="item in sections" :key="item.value" type="button" class="flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition" :class="activeSection === item.value ? 'bg-blue-600 font-medium text-white shadow-sm' : 'text-gray-500 hover:bg-white dark:text-gray-400 dark:hover:bg-gray-800'" @click="setSection(item.value)"><span>{{ item.icon }}</span>{{ item.label }}</button>
        </nav>
      </aside>

      <main class="min-w-0 p-4 sm:p-6">
        <header class="mb-6 flex items-start justify-between gap-4">
          <div><h1 class="text-xl font-bold text-gray-900 dark:text-gray-100">{{ pageTitle }}</h1><p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ pageSubtitle }}</p></div>
          <button type="button" class="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition hover:border-blue-200 hover:text-blue-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300" :disabled="homeLoading || sceneLoading" @click="refresh"><span :class="{ 'animate-spin': homeLoading || sceneLoading }">↻</span>刷新</button>
        </header>

        <div v-if="homeError" class="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300">{{ homeError }}，已保留最近一次成功内容。</div>
        <div v-if="reportsError && activeSection === 'reports'" class="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300">{{ reportsError }}，可稍后刷新重试。</div>

        <div v-if="homeLoading && !homePayload" class="space-y-3"><div v-for="n in 3" :key="n" class="h-24 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800" /></div>
        <template v-else-if="homePayload">
          <div v-show="activeSection === 'home'" class="space-y-7">
            <DataPortalOverview :attention="homePayload.attention" :activities="homePayload.recent_analysis" @open-activity="openActivity" />
            <DataPortalReportSection :reports="homePayload.report_summary.items" :summary="homePayload.report_summary" :compact="true" @open-report="openReport" />
            <DataPortalSceneSection v-if="scenePayload" :payload="scenePayload" :compact="true" @quick-question="openQuestion" />
            <DataPortalCatalogSection v-if="scenePayload" :payload="scenePayload" :compact="true" />
          </div>
          <DataPortalReportSection v-show="activeSection === 'reports'" :reports="allReports" :summary="homePayload.report_summary" :initial-filter="reportFilter" @filter-change="setReportFilter" @open-report="openReport" />
          <DataPortalSceneSection v-if="activeSection === 'scenes' && scenePayload" :payload="scenePayload" @quick-question="openQuestion" />
          <DataPortalCatalogSection v-if="activeSection === 'catalog' && scenePayload" :payload="scenePayload" @quick-question="openQuestion" />
        </template>

        <div v-if="sceneError && (activeSection === 'home' || activeSection === 'scenes' || activeSection === 'catalog')" class="mt-4 rounded-xl border border-dashed border-gray-200 px-4 py-6 text-center text-xs text-gray-400 dark:border-gray-800">{{ sceneError }}</div>
      </main>
    </div>

    <nav class="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t border-gray-100 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] shadow-lg backdrop-blur md:hidden dark:border-gray-800 dark:bg-gray-900/95">
      <button v-for="item in sections" :key="item.value" type="button" class="flex flex-col items-center gap-0.5 py-2 text-[11px]" :class="activeSection === item.value ? 'font-medium text-blue-600' : 'text-gray-400'" @click="setSection(item.value)"><span class="text-base">{{ item.icon }}</span>{{ item.mobileLabel }}</button>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataPortalOverview from "@/components/data-portal/DataPortalOverview.vue";
import DataPortalReportSection from "@/components/data-portal/DataPortalReportSection.vue";
import DataPortalSceneSection from "@/components/data-portal/DataPortalSceneSection.vue";
import DataPortalCatalogSection from "@/components/data-portal/DataPortalCatalogSection.vue";
import { useDataPortalHome } from "@/composables/useDataPortalHome";
import type { DataPortalActivity, DataPortalReportFilter, DataPortalReportItem } from "@/types/dataPortal";

type Section = "home" | "reports" | "scenes" | "catalog";
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false });
const route = useRoute();
const router = useRouter();
const sections: Array<{ value: Section; label: string; mobileLabel: string; icon: string }> = [
  { value: "home", label: "数据首页", mobileLabel: "首页", icon: "⌂" },
  { value: "reports", label: "我的报表", mobileLabel: "报表", icon: "▤" },
  { value: "scenes", label: "推荐场景", mobileLabel: "场景", icon: "✦" },
  { value: "catalog", label: "数据目录", mobileLabel: "目录", icon: "⌘" },
];
const initialSection = sections.some((item) => item.value === route.query.section) ? route.query.section as Section : "home";
const activeSection = ref<Section>(initialSection);
const { homePayload, scenePayload, allReports, homeLoading, sceneLoading, homeError, sceneError, reportsError, load, refresh } = useDataPortalHome();
const validReportFilters: DataPortalReportFilter[] = ["all", "subscribed", "pinned", "favorite", "shared", "recent"];
const reportFilter = ref<DataPortalReportFilter>(validReportFilters.includes(route.query.filter as DataPortalReportFilter) ? route.query.filter as DataPortalReportFilter : "all");
const current = computed(() => sections.find((item) => item.value === activeSection.value) || sections[0]);
const pageTitle = computed(() => activeSection.value === "home" ? "我的数据首页" : current.value.label);
const pageSubtitle = computed(() => activeSection.value === "home" ? "先看今天需要关注的数据，再继续最近的分析。" : "所有内容均基于当前账号的数据权限。" );

const setSection = (section: Section) => {
  activeSection.value = section;
  router.replace({ query: { ...route.query, section: section === "home" ? undefined : section } });
};
const setReportFilter = (filter: DataPortalReportFilter) => {
  reportFilter.value = filter;
  router.replace({ query: { ...route.query, filter: filter === "all" ? undefined : filter } });
};
const openReport = (report: DataPortalReportItem) => router.push({ path: "/dashboard/chat", query: { dataset_portal: "1", report_id: report.id } });
const openActivity = (activity: DataPortalActivity | Record<string, any>) => {
  if (activity.conversation_id) {
    router.push({ path: "/dashboard/chat", query: { conversation_id: activity.conversation_id } });
    return;
  }
  router.push({ path: "/dashboard/chat", query: { dataset_portal: "1", report_id: activity.report_id, run_id: activity.run_id } });
};
const openQuestion = (query: string, action: "send" | "fill") => router.push({ path: "/dashboard/chat", query: { portal_question: query, portal_action: action, source: "data_portal" } });

watch(() => route.query.section, (value) => {
  if (sections.some((item) => item.value === value)) activeSection.value = value as Section;
});
watch(() => route.query.filter, (value) => { reportFilter.value = validReportFilters.includes(value as DataPortalReportFilter) ? value as DataPortalReportFilter : "all"; });
onMounted(() => load(false));
</script>
