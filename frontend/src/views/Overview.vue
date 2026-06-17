<template>
  <div class="space-y-4 sm:space-y-6">
    <!-- Toast Notifications -->
    <Toast
      v-for="(toast, index) in toasts"
      :key="index"
      :message="toast.message"
      :type="toast.type"
      :duration="toast.duration"
      @close="removeToast(index)"
      :style="{ top: `${4 + index * 5}rem` }"
    />

    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-lg sm:text-2xl font-bold text-gray-900 tracking-tight">
        {{ userInfo?.role === "admin" ? "系统概览" : "我的工作台" }}
      </h1>
      <button
        @click="refreshData"
        :disabled="loading"
        class="inline-flex items-center justify-center p-2 sm:px-4 sm:py-2 border border-gray-200 rounded-xl shadow-sm text-sm font-bold text-gray-700 bg-white hover:bg-gray-50 active:scale-95 transition-all disabled:opacity-50"
        :title="loading ? '正在刷新...' : '刷新数据'"
      >
        <svg
          class="h-5 w-5 text-primary"
          :class="{ 'animate-spin': loading, 'sm:mr-2': true }"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2.5"
            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
          />
        </svg>
        <span class="hidden sm:inline">刷新数据</span>
      </button>
    </div>

    <!-- Loading State -->
    <div
      v-if="loading && !stats"
      class="flex items-center justify-center py-20"
    >
      <svg
        class="animate-spin h-12 w-12 text-gray-400"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle
          class="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          stroke-width="4"
        ></circle>
        <path
          class="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
        ></path>
      </svg>
    </div>

    <!-- Admin View -->
    <template v-else-if="userInfo?.role === 'admin'">
      <!-- Statistics Cards -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        <!-- Total Users -->
        <StatsCard
          title="总用户数"
          :value="stats?.total_users || 0"
          type="info"
        >
          <template #icon>
            <svg class="h-6 w-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          </template>
        </StatsCard>

        <!-- Active Users -->
        <StatsCard
          title="活跃用户"
          :value="stats?.active_users || 0"
          type="success"
          subtext="最近7天"
        >
          <template #icon>
            <svg class="h-6 w-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </template>
        </StatsCard>

        <!-- API Calls -->
        <StatsCard
          title="API 调用"
          :value="stats?.api_calls?.total || 0"
          type="purple"
        >
          <template #icon>
            <svg class="h-6 w-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </template>
          <template #subtext>
            <select
              v-model="period"
              @change="fetchAdminStats"
              class="text-xs border-none p-0 focus:ring-0 cursor-pointer bg-transparent"
            >
              <option value="today">今日</option>
              <option value="week">本周</option>
              <option value="month">本月</option>
            </select>
          </template>
        </StatsCard>

        <!-- Token Usage -->
        <StatsCard
          title="Token 消耗"
          :value="formatCompactNumber(stats?.total_tokens || 0)"
          type="warning"
        >
          <template #icon>
            <svg class="h-6 w-6 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
          </template>
          <template #subtext>
            <div class="flex flex-col gap-0.5 leading-tight mt-0.5">
              <span>输入 {{ formatCompactNumber(stats?.prompt_tokens || 0) }}</span>
              <span>输出 {{ formatCompactNumber(stats?.completion_tokens || 0) }}</span>
            </div>
          </template>
        </StatsCard>

        <!-- Success Rate Card -->
        <StatsCard
          title="成功率"
          :value="stats?.success_rate || 0"
          :type="(stats?.success_rate || 0) >= 90 ? 'success' : 'danger'"
          unit="%"
        >
          <template #icon>
            <svg
              class="h-6 w-6"
              :class="(stats?.success_rate || 0) >= 90 ? 'text-green-600' : 'text-red-600'"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </template>
          <template #subtext>
            {{ stats?.api_calls?.success || 0 }} / {{ stats?.api_calls?.total || 0 }}
          </template>
        </StatsCard>
      </div>

      <!-- Agent Run Health Section -->
      <AgentHealthPanel :agent-stats="agentStats" />

      <!-- Agent Performance and Token Distribution -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch">
        <div class="lg:col-span-2 h-full">
          <AgentPerformancePanel :agent-stats="agentStats" />
        </div>
        <div class="h-full">
          <AgentTokenDistributionChart :data="agentTokens" />
        </div>
      </div>

      <!-- Recent Users and API Calls -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Recent Users -->
        <RecentUsersCard :users="activities?.recent_users" />

        <!-- Recent API Calls -->
        <RecentCallsCard 
          :calls="activities?.recent_calls" 
          mode="compact" 
        />
      </div>

      <!-- Recent Failures (Admin Only) -->
      <RecentFailuresCard 
        :errors="agentStats?.recent_errors"
        @view-trace="goToAgentDebug"
      />
    </template>

    <!-- User View -->
    <template v-else>
      <!-- User Statistics Cards -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        <!-- API Key Status -->
        <StatsCard
          title="API Key"
          :value="stats?.api_key_status === 'active' ? '正常' : '已禁用'"
          :type="stats?.api_key_status === 'active' ? 'success' : 'danger'"
        >
          <template #icon>
            <svg
              class="h-6 w-6"
              :class="stats?.api_key_status === 'active' ? 'text-green-600' : 'text-red-600'"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
          </template>
        </StatsCard>

        <!-- My API Calls -->
        <StatsCard
          title="我的调用"
          :value="stats?.api_calls?.total || 0"
          type="info"
        >
          <template #icon>
             <svg class="h-6 w-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
          </template>
          <template #subtext>
            <select
              v-model="period"
              @change="fetchUserStats"
              class="text-xs border-none p-0 focus:ring-0 cursor-pointer bg-transparent"
            >
              <option value="today">今日</option>
              <option value="week">本周</option>
              <option value="month">本月</option>
            </select>
          </template>
        </StatsCard>

        <!-- Token Usage -->
        <StatsCard
          title="我的 Token"
          :value="formatCompactNumber(stats?.total_tokens || 0)"
          type="purple"
        >
          <template #icon>
            <svg class="h-6 w-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
          </template>
          <template #subtext>
            <div class="flex flex-col gap-0.5 leading-tight mt-0.5">
              <span>输入 {{ formatCompactNumber(stats?.prompt_tokens || 0) }}</span>
              <span>输出 {{ formatCompactNumber(stats?.completion_tokens || 0) }}</span>
            </div>
          </template>
        </StatsCard>

        <!-- Avg Response Time -->
        <StatsCard
          title="平均响应"
          :value="stats?.avg_response_time || 0"
          unit="ms"
          type="warning"
        >
          <template #icon>
             <svg class="h-6 w-6 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
          </template>
        </StatsCard>

        <!-- Success Rate -->
        <StatsCard
          title="成功率"
          :value="stats?.success_rate || 0"
          unit="%"
          :type="(stats?.success_rate || 0) >= 90 ? 'success' : 'danger'"
        >
          <template #icon>
            <svg
              class="h-6 w-6"
              :class="(stats?.success_rate || 0) >= 90 ? 'text-green-600' : 'text-red-600'"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </template>
        </StatsCard>
      </div>

      <!-- Agent Run Health Section (Rich View) -->
      <AgentHealthPanel :agent-stats="agentStats" />

      <!-- Agent Performance and Token Distribution (Rich View) -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch">
        <div class="lg:col-span-2 h-full">
          <AgentPerformancePanel :agent-stats="agentStats" />
        </div>
        <div class="h-full">
          <AgentTokenDistributionChart :data="agentTokens" />
        </div>
      </div>

      <!-- Quick Start -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100">
        <h2 class="text-base sm:text-lg font-bold text-gray-900 mb-4 flex items-center">
          <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
          快速开始
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
          <div class="border border-gray-100 rounded-xl p-4 bg-gray-50/50 hover:bg-gray-50 transition-colors">
            <div class="flex items-center mb-2">
              <div class="p-2 bg-blue-100 rounded-lg mr-3">
                <svg class="h-5 w-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <h3 class="font-bold text-gray-900 text-sm sm:text-base">API 文档</h3>
            </div>
            <p class="text-xs sm:text-sm text-gray-500 mb-3">
              查看完整的 API 接口文档和使用说明
            </p>
            <a
              :href="`${API_BASE}/docs`"
              target="_blank"
              class="inline-flex items-center text-xs sm:text-sm font-bold text-primary hover:underline"
            >
              查看文档 <svg class="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
            </a>
          </div>

          <div class="border border-gray-100 rounded-xl p-4 bg-gray-50/50 hover:bg-gray-50 transition-colors">
            <div class="flex items-center mb-2">
              <div class="p-2 bg-indigo-100 rounded-lg mr-3">
                <svg class="h-5 w-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </div>
              <h3 class="font-bold text-gray-900 text-sm sm:text-base">接口调试台</h3>
            </div>
            <p class="text-xs sm:text-sm text-gray-500 mb-3">
              在线测试 API 接口，快速验证功能
            </p>
            <router-link
              to="/dashboard/playground"
              class="inline-flex items-center text-xs sm:text-sm font-bold text-primary hover:underline"
            >
              开始调试 <svg class="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
            </router-link>
          </div>
        </div>
      </div>

      <!-- My Recent Calls -->
      <RecentCallsCard 
        title="我的最近调用"
        :calls="activities?.recent_calls" 
        mode="detailed" 
      />

      <!-- User Trend Chart -->
      <RequestTrendChart :trends="trends24h" />

    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import axios from "../utils/axios";
import Toast from "../components/Toast.vue";
import StatsCard from "../components/dashboard/StatsCard.vue";
import AgentHealthPanel from "../components/dashboard/AgentHealthPanel.vue";
import AgentPerformancePanel from "../components/dashboard/AgentPerformancePanel.vue";
import AgentTokenDistributionChart from "../components/dashboard/AgentTokenDistributionChart.vue";
import RecentUsersCard from "../components/dashboard/RecentUsersCard.vue";
import RecentCallsCard from "../components/dashboard/RecentCallsCard.vue";
import RequestTrendChart from "../components/dashboard/RequestTrendChart.vue";
import RecentFailuresCard from "../components/dashboard/RecentFailuresCard.vue";

const router = useRouter();
const API_BASE = ""; // Using relative path with axios instance proxy

const apiKey = ref(localStorage.getItem("api_key") || "");
const userInfo = ref<any>(null);
const loading = ref(false);
const period = ref("today");

const stats = ref<any>(null);
const agentStats = ref<any>(null);
const activities = ref<any>(null);
const trends24h = ref<any[]>([]);
const agentTokens = ref<any[]>([]);

const toasts = ref<
  Array<{
    message: string;
    type: "success" | "error" | "warning" | "info";
    duration?: number;
  }>
>([]);

const addToast = (
  message: string,
  type: "success" | "error" | "warning" | "info" = "info",
  duration = 3000
) => {
  toasts.value.push({ message, type, duration });
};

const removeToast = (index: number) => {
  toasts.value.splice(index, 1);
};

const fetchAgentStats = async () => {
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/agent-stats`,
      {
        headers: { "X-API-Key": apiKey.value },
        params: { period: period.value },
      }
    );
    agentStats.value = response.data;
  } catch (error) {
    console.error("Failed to fetch agent stats:", error);
  }
};

const fetchTrends24h = async () => {
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/api-trends-24h`,
      {
        headers: { "X-API-Key": apiKey.value },
      }
    );
    trends24h.value = response.data;
  } catch (error: any) {
    console.error("Failed to fetch 24h trends:", error);
  }
};

const fetchAdminStats = async () => {
  loading.value = true;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/admin-stats`,
      {
        headers: { "X-API-Key": apiKey.value },
        params: { period: period.value },
      }
    );
    stats.value = response.data;
  } catch (error: any) {
    addToast(error.response?.data?.detail || "加载统计数据失败", "error");
    console.error("Failed to fetch admin stats:", error);
  } finally {
    loading.value = false;
  }
};

const fetchUserStats = async () => {
  loading.value = true;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/user-stats`,
      {
        headers: { "X-API-Key": apiKey.value },
        params: { period: period.value },
      }
    );
    stats.value = response.data;
  } catch (error: any) {
    addToast(error.response?.data?.detail || "加载统计数据失败", "error");
    console.error("Failed to fetch user stats:", error);
  } finally {
    loading.value = false;
  }
};

const fetchRecentActivities = async () => {
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/recent-activities`,
      {
        headers: { "X-API-Key": apiKey.value },
        params: { limit: 10 },
      }
    );
    activities.value = response.data;
  } catch (error: any) {
    console.error("Failed to fetch recent activities:", error);
  }
};

const fetchAgentTokens = async () => {
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/token-stats/agents`,
      {
        headers: { "X-API-Key": apiKey.value },
        params: { period: period.value },
      }
    );
    agentTokens.value = response.data;
  } catch (error) {
    console.error("Failed to fetch agent token distribution:", error);
  }
};

const refreshData = async () => {
  if (userInfo.value?.role === "admin") {
    await fetchAdminStats();
    await fetchAgentStats();
  } else {
    await fetchUserStats();
    await fetchAgentStats();
  }
  await fetchRecentActivities();
  await fetchTrends24h();
  await fetchAgentTokens();
};

const goToAgentDebug = (traceId: string) => {
  router.push({
    path: "/dashboard/agent-debug",
    query: { traceId },
  });
};

const loadUserInfo = () => {
  const stored = localStorage.getItem("user_info");
  if (stored) {
    userInfo.value = JSON.parse(stored);
  }
};

const formatCompactNumber = (num: number) => {
  if (num === undefined || num === null) return "0";
  if (num >= 1.0e9) {
    return (num / 1.0e9).toFixed(2).replace(/\.00$/, "") + " B";
  }
  if (num >= 1.0e6) {
    return (num / 1.0e6).toFixed(2).replace(/\.00$/, "") + " M";
  }
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
};

onMounted(async () => {
  loadUserInfo();
  await refreshData();
});
</script>