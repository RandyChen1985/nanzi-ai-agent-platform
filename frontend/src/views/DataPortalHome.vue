<template>
  <div :class="props.embedded ? 'bg-white dark:bg-gray-900' : 'min-h-full bg-gray-50/70 pb-20 dark:bg-gray-950 md:pb-0'">
    <div class="grid grid-cols-1 overflow-hidden bg-white dark:bg-gray-900 md:grid-cols-[200px_minmax(0,1fr)]" :class="props.embedded ? 'min-h-[620px]' : 'min-h-[calc(100vh-4rem)]'">
      <!-- 左侧 Aside 垂直导航栏 -->
      <aside class="hidden border-r border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-900 md:block">
        <div class="mb-5 flex items-center gap-2 px-2 text-sm font-bold text-gray-900 dark:text-gray-100">
          <span class="grid h-8 w-8 place-items-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-500/20">▦</span>
          我的数据门户
        </div>
        <nav class="space-y-1">
          <button
            v-for="item in sections"
            :key="item.value"
            type="button"
            class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-all cursor-pointer"
            :class="
              activeSection === item.value
                ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20'
                : 'text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200'
            "
            @click="setSection(item.value)"
          >
            <span class="text-base">{{ item.icon }}</span>
            <span>{{ item.label }}</span>
          </button>
        </nav>
      </aside>

      <!-- 右侧主内容区域 -->
      <main class="min-w-0 p-4 sm:p-6">
        <header class="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h1 class="text-xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
              <span>{{ pageTitle }}</span>
              <button
                v-if="activeSection === 'reports'"
                type="button"
                class="flex h-6 w-6 items-center justify-center rounded-full border border-gray-200 bg-white text-blue-600 shadow-2xs hover:border-blue-300 hover:bg-blue-50 cursor-pointer"
                title="固化报表设计规范与使用指南"
                @click="showSpecsModal = true"
              >
                <span class="text-xs font-bold">?</span>
              </button>
            </h1>
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ pageSubtitle }}</p>
          </div>

          <div class="flex items-center gap-2">
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 transition hover:border-blue-200 hover:text-blue-600 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 cursor-pointer"
              :disabled="homeLoading || sceneLoading || reportsLoading"
              @click="refresh"
            >
              <span :class="{ 'animate-spin': homeLoading || sceneLoading || reportsLoading }">↻</span>
              <span>刷新</span>
            </button>
          </div>
        </header>

        <!-- 错误提示 -->
        <div v-if="homeError" class="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300">
          {{ homeError }}，已保留最近一次成功内容。
        </div>
        <div v-if="reportsError && activeSection === 'reports'" class="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300">
          {{ reportsError }}，可稍后刷新重试。
        </div>

        <!-- 加载骨架屏 -->
        <div v-if="activeSection === 'home' && homeLoading && !homePayload" class="space-y-4">
          <div v-for="n in 3" :key="n" class="h-28 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800" />
        </div>
        <div v-else-if="activeSection === 'reports' && reportsLoading && !allReports.length" class="space-y-4">
          <div v-for="n in 3" :key="n" class="h-28 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800" />
        </div>

        <!-- 主内容区域 -->
        <template v-else>
          <!-- 1. 数据首页 Tab -->
          <div v-if="activeSection === 'home' && homePayload" class="space-y-7 animate-fade-in">
            <DataPortalOverview :attention="homePayload.attention" :activities="homePayload.recent_analysis" @open-activity="openActivity" />
            <DataPortalReportSection
              :reports="homePayload.report_summary.items"
              :summary="homePayload.report_summary"
              :compact="true"
              @open-report="openReport"
              @create-report="openCreateReport"
              @open-specs="showSpecsModal = true"
            />
            <DataPortalSceneSection v-if="scenePayload" :payload="scenePayload" :compact="true" @quick-question="openQuestion" />
            <DataPortalCatalogSection v-if="scenePayload" :payload="scenePayload" :compact="true" />
          </div>

          <!-- 2. 固化报表 Tab -->
          <div v-if="activeSection === 'reports'" class="animate-fade-in">
            <DataPortalReportSection
              :reports="allReports"
              :summary="reportSummary"
              :manage="true"
              :initial-filter="reportFilter"
              @filter-change="setReportFilter"
              @open-report="openReport"
              @create-report="openCreateReport"
              @execute="executeReport"
              @detail="openReportInfo"
              @edit="openEditReport"
              @favorite="toggleReportFavorite"
              @pin="toggleReportPinned"
              @share="openReportInfo"
              @copy="copyReport"
              @delete="requestDeleteReport"
              @subscription="openReportSubscription"
              @open-specs="showSpecsModal = true"
            />
          </div>

          <!-- 3. 推荐场景 Tab -->
          <div v-if="activeSection === 'scenes'" class="animate-fade-in">
            <DataPortalSceneSection v-if="scenePayload" :payload="scenePayload" @quick-question="openQuestion" />
          </div>

          <!-- 4. 数据目录 Tab -->
          <div v-if="activeSection === 'catalog'" class="animate-fade-in">
            <DataPortalCatalogSection v-if="scenePayload" :payload="scenePayload" @quick-question="openQuestion" />
          </div>
        </template>

        <div v-if="sceneError && (activeSection === 'home' || activeSection === 'scenes' || activeSection === 'catalog')" class="rounded-xl border border-dashed border-gray-200 p-8 text-center text-xs text-gray-400 dark:border-gray-800">
          {{ sceneError }}
        </div>
      </main>
    </div>

    <!-- 移动端底部 Tab 栏 -->
    <nav v-if="!delegateNavigation" class="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t border-gray-100 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] shadow-lg backdrop-blur md:hidden dark:border-gray-800 dark:bg-gray-900/95">
      <button
        v-for="item in sections"
        :key="item.value"
        type="button"
        class="flex flex-col items-center gap-0.5 py-2 text-[11px]"
        :class="activeSection === item.value ? 'font-medium text-blue-600' : 'text-gray-400'"
        @click="setSection(item.value)"
      >
        <span class="text-base">{{ item.icon }}</span>
        <span>{{ item.mobileLabel }}</span>
      </button>
    </nav>

    <!-- 新建固化报表 Modal -->
    <DataPortalReportCreateModal
      :visible="showCreateModal"
      :report="editingReport"
      @close="closeReportModal"
      @created="handleReportCreated"
    />

    <!-- 固化报表设计规范与使用指南 Modal -->
    <div
      v-if="showSpecsModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in"
      @click.self="showSpecsModal = false"
    >
      <div
        class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-4xl h-[80vh] flex flex-col overflow-hidden border border-gray-100 dark:border-gray-800"
      >
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 dark:border-gray-800 flex justify-between items-center bg-blue-50/40 dark:bg-gray-800/60">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center shadow-md shadow-blue-500/20">
              <span class="text-lg">▤</span>
            </div>
            <div>
              <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100">固化报表设计规范与使用指南</h2>
              <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">标准化 SQL 资产沉淀、自动化定时订阅、多渠道分发与全员共享协同机制。</p>
            </div>
          </div>
          <button @click="showSpecsModal = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 cursor-pointer">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Tabs -->
        <div class="flex border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-6">
          <button
            v-for="tab in ['concept', 'workflow', 'practice']"
            :key="tab"
            @click="activeSpecsTab = tab as any"
            class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
            :class="activeSpecsTab === tab ? 'border-blue-600 text-blue-700 dark:text-blue-400' : 'border-transparent text-gray-500 hover:text-gray-700'"
          >
            {{ tab === 'concept' ? '核心理念与价值 (Core Concept)' :
               tab === 'workflow' ? '报表开发与沉淀流 (Development Workflow)' : '调度订阅与最佳实践 (Subscription & Best Practice)' }}
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50 dark:bg-gray-950/40 text-xs space-y-6">
          <!-- Tab 1: Concept -->
          <div v-if="activeSpecsTab === 'concept'" class="space-y-4 max-w-3xl">
            <div class="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950/30 dark:to-indigo-950/20 border-l-4 border-blue-600 p-4 rounded-r-xl">
              <h3 class="font-bold text-blue-900 dark:text-blue-200 mb-1">什么是「固化报表」？</h3>
              <p class="text-gray-600 dark:text-gray-300 leading-relaxed">
                固化报表（Saved Reports）是企业级数据资产的“标准件”。它将一次性、偶发性的 AI 即席查数（Ad-hoc Query）或专业数据分析师编写的高质量 SQL，经过验证后**“固化”**为具备明确业务口径、权限受控、可重复执行的标准报表。
              </p>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div class="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 space-y-2">
                <h4 class="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
                  <span class="text-blue-500">✦</span> 1. 双轨沉淀机制
                </h4>
                <p class="text-gray-500 dark:text-gray-400 leading-relaxed">
                  既支持在 ChatBI 对话查数成功后一键「添加固化报表」，也支持在报表中心直接点击「新建固化报表」手写 SQL 录入。
                </p>
              </div>
              <div class="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 space-y-2">
                <h4 class="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
                  <span class="text-indigo-500">✦</span> 2. 自动化调度与触达
                </h4>
                <p class="text-gray-500 dark:text-gray-400 leading-relaxed">
                  可针对任意固化报表配置定时调度订阅，支持每日/每周/每月定时执行，并自动推送到站内信、企微群或邮件。
                </p>
              </div>
            </div>
          </div>

          <!-- Tab 2: Workflow -->
          <div v-else-if="activeSpecsTab === 'workflow'" class="space-y-4 max-w-3xl">
            <div class="bg-white dark:bg-gray-900 p-6 rounded-2xl border border-gray-200 dark:border-gray-800 space-y-4">
              <h4 class="font-bold text-gray-900 dark:text-gray-100 text-sm">标准化报表开发四步法</h4>
              <div class="space-y-3">
                <div class="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-xl">
                  <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white font-bold text-[10px]">1</span>
                  <div>
                    <strong class="text-gray-800 dark:text-gray-200">数据源选择与权限校验</strong>
                    <p class="text-gray-500 dark:text-gray-400 mt-0.5">选择报表所针对的物理数据源连接或元数据数据集，系统自动进行只读鉴权反查。</p>
                  </div>
                </div>
                <div class="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-xl">
                  <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white font-bold text-[10px]">2</span>
                  <div>
                    <strong class="text-gray-800 dark:text-gray-200">SQL 编写与在线试跑</strong>
                    <p class="text-gray-500 dark:text-gray-400 mt-0.5">在编辑器中输入 SELECT 语句，点击【▶ 试跑测试 SQL】校验语法并预览前 50 条真实数据。</p>
                  </div>
                </div>
                <div class="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-xl">
                  <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white font-bold text-[10px]">3</span>
                  <div>
                    <strong class="text-gray-800 dark:text-gray-200">业务口径与标签打标</strong>
                    <p class="text-gray-500 dark:text-gray-400 mt-0.5">录入业务统计口径说明与分类标签（如“财务, 营收, 月报”），方便全员检索。</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 3: Practice -->
          <div v-else-if="activeSpecsTab === 'practice'" class="space-y-4 max-w-3xl">
            <div class="bg-white dark:bg-gray-900 p-6 rounded-2xl border border-gray-200 dark:border-gray-800 space-y-4">
              <h4 class="font-bold text-gray-900 dark:text-gray-100 text-sm">运维与共享最佳实践</h4>
              <ul class="space-y-2.5 text-gray-600 dark:text-gray-300 list-disc pl-5 leading-relaxed">
                <li><strong>避免全表无分页扫描</strong>：建议在报表 SQL 中显式指定聚合维度（GROUP BY）或合理的 LIMIT 条数；</li>
                <li><strong>动态时间报表</strong>：系统会自动识别 SQL 中的固定日期条件，并支持在后续执行时选择动态时间窗口（如“本月”、“最近 7 天”）；</li>
                <li><strong>协同共享</strong>：报表创建者可在报表详情中将其「共享」给部门或全员，共享后其他用户将获得执行与订阅权限，但无法修改原始 SQL。</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>

    <ConfirmModal
      v-if="deletingReport"
      title="确认删除固化报表"
      :message="`确定删除「${deletingReport.title}」吗？删除后将无法恢复。`"
      confirm-text="确认删除"
      @confirm="confirmDeleteReport"
      @cancel="deletingReport = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import axios from "@/utils/axios";
import { useToast } from "@/composables/useToast";
import ConfirmModal from "@/components/ConfirmModal.vue";
import DataPortalOverview from "@/components/data-portal/DataPortalOverview.vue";
import DataPortalReportSection from "@/components/data-portal/DataPortalReportSection.vue";
import DataPortalSceneSection from "@/components/data-portal/DataPortalSceneSection.vue";
import DataPortalCatalogSection from "@/components/data-portal/DataPortalCatalogSection.vue";
import DataPortalReportCreateModal from "@/components/data-portal/DataPortalReportCreateModal.vue";
import { useDataPortalHome } from "@/composables/useDataPortalHome";
import type { DataPortalActivity, DataPortalReportFilter, DataPortalReportItem } from "@/types/dataPortal";

type Section = "home" | "reports" | "scenes" | "catalog";

const props = withDefaults(
  defineProps<{
    embedded?: boolean;
    delegateNavigation?: boolean;
  }>(),
  {
    embedded: false,
    delegateNavigation: false,
  }
);

const emit = defineEmits<{
  (e: "open-report", payload: { report_id: string | number; run_id?: string | number; detail_tab?: "info" | "runs" | "subscription"; run_now?: boolean }): void;
  (e: "open-conversation", payload: { conversation_id: string }): void;
  (e: "open-question", payload: { query: string; action: "send" | "fill" }): void;
}>();

const route = useRoute();
const router = useRouter();
const { showToast } = useToast();

const showCreateModal = ref(false);
const editingReport = ref<DataPortalReportItem | null>(null);
const showSpecsModal = ref(false);
const deletingReport = ref<DataPortalReportItem | null>(null);
const activeSpecsTab = ref<"concept" | "workflow" | "practice">("concept");

const sections: Array<{ value: Section; label: string; mobileLabel: string; icon: string }> = [
  { value: "home", label: "数据首页", mobileLabel: "首页", icon: "⌂" },
  { value: "reports", label: "固化报表", mobileLabel: "报表", icon: "▤" },
  { value: "scenes", label: "推荐场景", mobileLabel: "场景", icon: "✦" },
  { value: "catalog", label: "数据目录", mobileLabel: "目录", icon: "⌘" },
];

const initialSection = sections.some((item) => item.value === route.query.section)
  ? (route.query.section as Section)
  : "home";
const activeSection = ref<Section>(initialSection);
const {
  homePayload,
  scenePayload,
  allReports,
  homeLoading,
  sceneLoading,
  reportsLoading,
  homeError,
  sceneError,
  reportsError,
  load,
  refresh,
} = useDataPortalHome();

const reportSummary = computed(() => ({
  subscribed: allReports.value.filter((report) => !!report.subscription_status).length,
  pinned: allReports.value.filter((report) => !!report.pinned_at).length,
  favorite: allReports.value.filter((report) => !!report.is_favorite).length,
  shared: allReports.value.filter((report) => !report.is_owner).length,
  recent: allReports.value.filter((report) => !!report.last_run_at).length,
  items: allReports.value,
}));

const validReportFilters: DataPortalReportFilter[] = ["all", "subscribed", "pinned", "favorite", "shared", "recent"];
const reportFilter = ref<DataPortalReportFilter>(
  validReportFilters.includes(route.query.filter as DataPortalReportFilter)
    ? (route.query.filter as DataPortalReportFilter)
    : "all"
);

const current = computed(() => sections.find((item) => item.value === activeSection.value) || sections[0]);
const pageTitle = computed(() =>
  activeSection.value === "home" ? "我的数据首页" : activeSection.value === "reports" ? "固化报表中心" : current.value?.label || "我的数据门户"
);
const pageSubtitle = computed(() =>
  activeSection.value === "home"
    ? "先看今天需要关注的数据，再继续最近的分析。"
    : activeSection.value === "reports"
    ? "管理已沉淀与手动开发的固化报表、执行与订阅调度管理。"
    : "所有内容均基于当前账号的数据权限。"
);

const setSection = (section: Section) => {
  activeSection.value = section;
  if (!props.delegateNavigation) {
    router.replace({ query: { ...route.query, section: section === "home" ? undefined : section } });
  }
};

const setReportFilter = (filter: DataPortalReportFilter) => {
  reportFilter.value = filter;
  if (!props.delegateNavigation) {
    router.replace({ query: { ...route.query, filter: filter === "all" ? undefined : filter } });
  }
};

type ReportDetailTab = "info" | "runs" | "subscription";

const openReportAt = (report: DataPortalReportItem, detailTab?: ReportDetailTab, runId?: string | number, runNow = false) => {
  const payload = {
    report_id: report.id,
    ...(runId != null ? { run_id: runId } : {}),
    ...(detailTab ? { detail_tab: detailTab } : {}),
    ...(runNow ? { run_now: true } : {}),
  };
  if (props.delegateNavigation) {
    emit("open-report", payload);
    return;
  }
  router.push({
    path: "/dashboard/chat",
    query: {
      dataset_portal: "1",
      report_id: report.id,
      ...(runId != null ? { run_id: String(runId) } : {}),
      ...(detailTab ? { report_detail_tab: detailTab } : {}),
      ...(runNow ? { run_now: "1" } : {}),
    },
  });
};

const openReport = (report: DataPortalReportItem) => openReportAt(report);
const openReportInfo = (report: DataPortalReportItem) => openReportAt(report, "info");
const openReportSubscription = (report: DataPortalReportItem) => openReportAt(report, "subscription");

const executeReport = async (report: DataPortalReportItem) => {
  const reportState = report as DataPortalReportItem & {
    run_permission_status?: string;
    run_permission_message?: string;
    default_params?: Record<string, unknown>;
  };
  if (reportState.run_permission_status === "denied") {
    showToast(reportState.run_permission_message || "暂无该报表所需数据权限，无法运行。", "warning");
    return;
  }
  showToast("已打开统一运行面板，请先确认参数和权限后执行", "success");
  openReportAt(report, "info", undefined, true);
};

const openCreateReport = () => {
  editingReport.value = null;
  showCreateModal.value = true;
};

const openEditReport = (report: DataPortalReportItem) => {
  editingReport.value = report;
  showCreateModal.value = true;
};

const closeReportModal = () => {
  showCreateModal.value = false;
  editingReport.value = null;
};

const updateReportPreference = async (report: DataPortalReportItem, payload: Record<string, unknown>) => {
  try {
    await axios.put(`/api/portal/saved-reports/${report.id}/prefs`, payload);
    await refresh();
  } catch (error: any) {
    showToast(error.response?.data?.detail || "报表偏好更新失败", "error");
  }
};

const toggleReportFavorite = (report: DataPortalReportItem) =>
  updateReportPreference(report, { is_favorite: !report.is_favorite });

const toggleReportPinned = (report: DataPortalReportItem) =>
  updateReportPreference(report, { pinned: !report.pinned_at });

const copyReport = async (report: DataPortalReportItem) => {
  try {
    await axios.post(`/api/portal/saved-reports/${report.id}/copy`);
    showToast("已复制为我的固化报表", "success");
    await refresh();
  } catch (error: any) {
    showToast(error.response?.data?.detail || "复制报表失败", "error");
  }
};

const requestDeleteReport = (report: DataPortalReportItem) => {
  if (!report.is_owner) return;
  deletingReport.value = report;
};

const confirmDeleteReport = async () => {
  const report = deletingReport.value;
  if (!report) return;
  try {
    await axios.delete(`/api/portal/saved-reports/${report.id}`);
    showToast("固化报表已删除", "success");
    await refresh();
  } catch (error: any) {
    showToast(error.response?.data?.detail || "删除报表失败", "error");
  } finally {
    deletingReport.value = null;
  }
};

const openActivity = (activity: DataPortalActivity | Record<string, any>) => {
  if (activity.conversation_id) {
    if (props.delegateNavigation) {
      emit("open-conversation", { conversation_id: String(activity.conversation_id) });
      return;
    }
    router.push({ path: "/dashboard/chat", query: { conversation_id: activity.conversation_id } });
    return;
  }
  if (props.delegateNavigation) {
    emit("open-report", { report_id: activity.report_id, run_id: activity.run_id });
    return;
  }
  router.push({
    path: "/dashboard/chat",
    query: { dataset_portal: "1", report_id: activity.report_id, run_id: activity.run_id },
  });
};

const openQuestion = (query: string, action: "send" | "fill") => {
  if (props.delegateNavigation) {
    emit("open-question", { query, action });
    return;
  }
  router.push({ path: "/dashboard/chat", query: { portal_question: query, portal_action: action, source: "data_portal" } });
};

const handleReportCreated = () => {
  closeReportModal();
  refresh();
};

watch(
  () => route.query.section,
  (value) => {
    if (sections.some((item) => item.value === value)) activeSection.value = value as Section;
  }
);

watch(
  () => route.query.filter,
  (value) => {
    reportFilter.value = validReportFilters.includes(value as DataPortalReportFilter)
      ? (value as DataPortalReportFilter)
      : "all";
  }
);

onMounted(() => load(false));
</script>
