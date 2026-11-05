<template>
  <div class="space-y-4 sm:space-y-5">
    <!-- Toast Notifications -->
    <Toast
      v-for="(toast, index) in toasts"
      :key="toast.id"
      :message="toast.message"
      :type="toast.type"
      :duration="toast.duration"
      @close="removeToast(toast.id)"
      :style="{ top: `${4 + index * 5}rem` }"
    />

    <!-- Header / Toolbar -->
    <header>
      <div class="flex items-center justify-between gap-3">
        <h1 class="min-w-0 text-2xl font-bold tracking-normal text-gray-900">
          {{ userInfo?.role === "admin" ? "系统概览" : "我的概览" }}
        </h1>
        <div class="overview-toolbar flex shrink-0 items-center gap-1.5">
          <!-- 统计区间：默认今日，快捷项偏自然周期（本周/上周/本月/上月），也可翻日历任选 -->
          <DateRangePicker
            v-model="dateRange"
            :max-days="90"
            :preset-keys="OVERVIEW_PRESET_KEYS"
            @change="onRangeChange"
          />

          <!--
            刷新 + 更多：与日期卡片同一套描边、圆角与高度（h-9 / rounded-xl / border-gray-200），
            放在一起才像一组工具。「部署检查」原来是独立按钮，收进这个下拉里，
            管理员之外不显示下拉箭头。
          -->
          <div ref="refreshMenuRef" class="relative flex">
            <div class="flex h-9 items-stretch overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-colors hover:border-primary/40">
              <button
                type="button"
                class="overview-refresh-btn flex items-center gap-1.5 px-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="loading"
                :aria-busy="loading"
                :aria-label="loading ? '刷新中' : '刷新概览数据'"
                @click="refreshData"
              >
                <svg
                  class="h-3.5 w-3.5"
                  :class="{ 'animate-spin': loading }"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
                <!-- 窄屏只留图标：和日期卡片并排时，中文会把工具栏挤出去 -->
                <span class="hidden sm:inline">{{ loading ? '刷新中' : '刷新' }}</span>
              </button>

              <template v-if="userInfo?.role === 'admin'">
                <span class="w-px bg-gray-200" aria-hidden="true"></span>
                <button
                  type="button"
                  class="flex w-8 items-center justify-center text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-700"
                  :aria-expanded="refreshMenuOpen"
                  aria-haspopup="menu"
                  aria-label="更多操作"
                  title="更多操作"
                  @click="refreshMenuOpen = !refreshMenuOpen"
                >
                  <svg
                    class="h-3.5 w-3.5 transition-transform"
                    :class="{ 'rotate-180': refreshMenuOpen }"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    viewBox="0 0 24 24"
                  >
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </button>
              </template>
            </div>

            <div
              v-if="refreshMenuOpen && userInfo?.role === 'admin'"
              class="absolute right-0 top-full z-30 mt-1 w-40 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-xl"
              role="menu"
            >
              <button
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
                role="menuitem"
                @click="runDeploymentCheck"
              >部署检查</button>
            </div>
          </div>
        </div>
      </div>
    </header>

    <DeploymentChecklist v-if="userInfo?.role === 'admin'" hide-when-complete />

    <!-- Loading State -->
    <div
      v-if="loading && !stats"
      class="flex items-center justify-center py-20"
    >
      <svg
        class="animate-spin h-10 w-10 text-gray-300"
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
      <!-- 主角卡承担「当前是否健康」这个问题，其余四个退为背景信息 -->
      <div class="grid grid-cols-1 gap-3.5 lg:grid-cols-[1.62fr_repeat(4,minmax(0,1fr))]">
        <MetricHeroCard
          label="接口成功率"
          :value="successPercent.toFixed(2)"
          unit="%"
          :percent="successPercent"
          :total="apiTotal"
          :success="apiSuccess"
          :failed="apiFailed"
        />
        <MetricMiniCard label="API 调用" icon-color="#7c3aed" :value="apiTotal.toLocaleString()" :foot="rangeFoot">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
          </template>
        </MetricMiniCard>
        <MetricMiniCard label="Token 消耗" icon-color="#ea580c" :value="formatTokenCompact(stats?.total_tokens || 0)" foot="含输入与输出">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
          </template>
        </MetricMiniCard>
        <MetricMiniCard label="用户" icon-color="#2563eb" :value="totalUsers" :foot="activeUsersFoot">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
          </template>
        </MetricMiniCard>
        <MetricMiniCard
          label="工具成功率" icon-color="#059669"
          :value="toolSuccessRate.toFixed(2)"
          unit="%"
          :foot="`${toolCallsTotal} 次工具调用`"
        >
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          </template>
        </MetricMiniCard>
      </div>

      <DashboardAlertStrip
        v-if="apiFailed > 0"
        :count="apiFailed"
        :subjects="failureSubjects"
        :scope="rangeScope"
        :period-label="rangeName"
      />

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-[1.32fr_1fr]">
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>工具调用分布</h3>
            <span class="text-xs text-gray-400">{{ rangeName }} · 共 {{ toolCallsTotal }} 次</span>
          </div>
          <div class="ov-card-body">
            <ToolUsageRanking :items="toolRankingItems" :total="toolCallsTotal">
              <template #footer>
                <div v-if="recentErrors.length" class="mt-4 border-t border-dashed border-gray-200 pt-3.5">
                  <div class="mb-2 flex items-center justify-between">
                    <span class="text-[11.5px] font-semibold text-gray-400">最近失败</span>
                    <button type="button" class="text-[11px] font-semibold text-primary hover:underline" @click="openFailureDetails">全部 →</button>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <button
                      v-for="(err, index) in recentErrors"
                      :key="`${err.trace_id}-${index}`"
                      type="button"
                      class="flex items-center gap-2 text-left text-[11.5px] transition-colors hover:text-primary"
                      @click="err.trace_id && goToAgentDebug(err.trace_id)"
                    >
                      <span class="h-1 w-1 shrink-0 rounded-full bg-red-500"></span>
                      <span class="shrink-0 font-mono text-gray-700">{{ err.tool || '未知工具' }}</span>
                      <span class="min-w-0 flex-1 truncate text-gray-400">{{ err.message || '执行失败' }}</span>
                      <span class="shrink-0 text-gray-400">{{ formatClock(err.time) }}</span>
                    </button>
                  </div>
                </div>
              </template>
            </ToolUsageRanking>
          </div>
        </div>

        <div class="flex flex-col gap-4">
          <div class="ov-card">
            <div class="ov-card-head">
              <h3 class="ov-card-title"><span class="ov-dot"></span>执行耗时趋势</h3>
              <span class="text-xs text-gray-400">24h · 峰值 {{ maxLatency.toLocaleString() }}ms</span>
            </div>
            <div class="ov-card-body">
              <LatencyTrendPanel :points="latencyPoints" :threshold="LATENCY_THRESHOLD" />
            </div>
          </div>

          <div class="ov-card">
            <div class="ov-card-head">
              <h3 class="ov-card-title"><span class="ov-dot"></span>核心指标</h3>
            </div>
            <div class="ov-card-body pt-2 pb-4">
              <CoreMetricsPanel :items="healthKpis" />
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>智能体性能排行</h3>
            <span class="text-xs text-gray-400">按调用量</span>
          </div>
          <div class="ov-card-body pt-3">
            <AgentPerformanceTable :rows="agentPerfRows" />
          </div>
        </div>
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>Token 消耗分布</h3>
            <span class="text-xs text-gray-400">{{ rangeName }} · 共 {{ formatTokenCompact(stats?.total_tokens || 0) }}</span>
          </div>
          <div class="ov-card-body pt-4">
            <AgentTokenBars :items="tokenBarItems" />
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RecentUsersCard :users="activities?.recent_users" />
        <RecentCallsCard :calls="activities?.recent_calls" mode="compact" />
      </div>

      <RequestTrendChart :trends="trends24h" />
    </template>

    <!-- User View -->
    <template v-else-if="userInfo">
      <div class="grid grid-cols-1 gap-3.5 lg:grid-cols-[1.62fr_repeat(4,minmax(0,1fr))]">
        <MetricHeroCard
          label="我的成功率"
          :value="successPercent.toFixed(2)"
          unit="%"
          :percent="successPercent"
          :total="apiTotal"
          :success="apiSuccess"
          :failed="apiFailed"
        />
        <MetricMiniCard label="我的调用" icon-color="#7c3aed" :value="apiTotal.toLocaleString()" :foot="rangeFoot">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
          </template>
        </MetricMiniCard>
        <MetricMiniCard label="我的 Token" icon-color="#ea580c" :value="formatTokenCompact(stats?.total_tokens || 0)" foot="含输入与输出">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
          </template>
        </MetricMiniCard>
        <MetricMiniCard label="平均响应" icon-color="#059669" :value="(stats?.avg_response_time || 0).toLocaleString()" unit="ms" foot="接口平均耗时">
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          </template>
        </MetricMiniCard>
        <!-- API Key 状态是配置项而非数量，用文字与颜色表达，不做成数字 -->
        <MetricMiniCard
          label="API Key" icon-color="#2563eb"
          :value="apiKeyLabel"
          :value-class="apiKeyActive ? 'text-[19px] text-emerald-700' : 'text-[19px] text-red-600'"
          :foot="apiKeyActive ? '可正常调用接口' : '已被禁用，请联系管理员'"
        >
          <template #icon>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-full w-full"><path d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" /></svg>
          </template>
        </MetricMiniCard>
      </div>

      <DashboardAlertStrip
        v-if="apiFailed > 0"
        :count="apiFailed"
        :subjects="failureSubjects"
        :scope="rangeScope"
        :period-label="rangeName"
      />

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-[1.32fr_1fr]">
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>我的工具使用</h3>
            <span class="text-xs text-gray-400">{{ rangeName }} · 共 {{ toolCallsTotal }} 次</span>
          </div>
          <div class="ov-card-body">
            <ToolUsageRanking :items="toolRankingItems" :total="toolCallsTotal">
              <template #footer>
                <div v-if="recentErrors.length" class="mt-4 border-t border-dashed border-gray-200 pt-3.5">
                  <div class="mb-2 flex items-center justify-between">
                    <span class="text-[11.5px] font-semibold text-gray-400">我的最近失败</span>
                    <button type="button" class="text-[11px] font-semibold text-primary hover:underline" @click="openFailureDetails">全部 →</button>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <button
                      v-for="(err, index) in recentErrors"
                      :key="`${err.trace_id}-${index}`"
                      type="button"
                      class="flex items-center gap-2 text-left text-[11.5px] transition-colors hover:text-primary"
                      @click="err.trace_id && goToAgentDebug(err.trace_id)"
                    >
                      <span class="h-1 w-1 shrink-0 rounded-full bg-red-500"></span>
                      <span class="shrink-0 font-mono text-gray-700">{{ err.tool || '未知工具' }}</span>
                      <span class="min-w-0 flex-1 truncate text-gray-400">{{ err.message || '执行失败' }}</span>
                      <span class="shrink-0 text-gray-400">{{ formatClock(err.time) }}</span>
                    </button>
                  </div>
                </div>
              </template>
            </ToolUsageRanking>
          </div>
        </div>

        <div class="flex flex-col gap-4">
          <div class="ov-card">
            <div class="ov-card-head">
              <h3 class="ov-card-title"><span class="ov-dot"></span>我的耗时趋势</h3>
              <span class="text-xs text-gray-400">24h · 峰值 {{ maxLatency.toLocaleString() }}ms</span>
            </div>
            <div class="ov-card-body">
              <LatencyTrendPanel :points="latencyPoints" :threshold="LATENCY_THRESHOLD" line-color="#60a5fa" />
            </div>
          </div>

          <div class="ov-card">
            <div class="ov-card-head">
              <h3 class="ov-card-title"><span class="ov-dot"></span>我的指标</h3>
            </div>
            <div class="ov-card-body pt-2 pb-4">
              <CoreMetricsPanel :items="healthKpis" />
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>智能体性能</h3>
            <span class="text-xs text-gray-400">我常用的</span>
          </div>
          <div class="ov-card-body pt-3">
            <AgentPerformanceTable :rows="agentPerfRows" />
          </div>
        </div>
        <div class="ov-card">
          <div class="ov-card-head">
            <h3 class="ov-card-title"><span class="ov-dot"></span>我的 Token 分布</h3>
            <span class="text-xs text-gray-400">{{ rangeName }} · 共 {{ formatTokenCompact(stats?.total_tokens || 0) }}</span>
          </div>
          <div class="ov-card-body pt-4">
            <AgentTokenBars :items="tokenBarItems" />
          </div>
        </div>
      </div>

      <!-- Quick Start -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-5 border border-gray-100">
        <h2 class="text-sm font-bold text-gray-900 mb-3.5 flex items-center">
          <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
          快速开始
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 sm:gap-4">
          <div class="rounded-xl border border-gray-100 p-4 bg-gray-50/40 hover:bg-gray-50 transition-colors">
            <div class="flex items-center mb-2">
              <div class="p-2 bg-blue-50 rounded-lg mr-3">
                <svg class="h-4 w-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <h3 class="font-bold text-gray-900 text-sm">API 文档</h3>
            </div>
            <p class="text-xs text-gray-500 mb-3">
              查看完整的 API 接口文档和使用说明
            </p>
            <a
              :href="`${API_BASE}/docs`"
              target="_blank"
              class="inline-flex items-center text-xs font-semibold text-primary hover:underline"
            >
              查看文档
              <svg class="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </a>
          </div>

          <div class="rounded-xl border border-gray-100 p-4 bg-gray-50/40 hover:bg-gray-50 transition-colors">
            <div class="flex items-center mb-2">
              <div class="p-2 bg-violet-50 rounded-lg mr-3">
                <svg class="h-4 w-4 text-violet-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </div>
              <h3 class="font-bold text-gray-900 text-sm">接口调试台</h3>
            </div>
            <p class="text-xs text-gray-500 mb-3">
              在线测试 API 接口，快速验证功能
            </p>
            <router-link
              to="/dashboard/playground"
              class="inline-flex items-center text-xs font-semibold text-primary hover:underline"
            >
              开始调试
              <svg class="ml-1 w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </router-link>
          </div>
        </div>
      </div>

      <RecentCallsCard
        title="我的最近调用"
        :calls="activities?.recent_calls"
        mode="detailed"
      />

      <RequestTrendChart :trends="trends24h" />
    </template>

    <!-- 身份未知（例如本地缓存缺失）：不降级渲染成普通用户视图，否则管理员会看到错误界面 -->
    <div v-else class="rounded-2xl border border-amber-100 bg-amber-50/60 p-6 text-center">
      <p class="text-sm font-semibold text-amber-800">未能识别当前用户身份</p>
      <p class="mt-1 text-xs text-amber-700">请刷新页面重试；若仍然如此，请重新登录。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import axios from "../utils/axios";
import Toast from "../components/Toast.vue";
import MetricHeroCard from "../components/dashboard/MetricHeroCard.vue";
import MetricMiniCard from "../components/dashboard/MetricMiniCard.vue";
import ToolUsageRanking from "../components/dashboard/ToolUsageRanking.vue";
import LatencyTrendPanel from "../components/dashboard/LatencyTrendPanel.vue";
import CoreMetricsPanel from "../components/dashboard/CoreMetricsPanel.vue";
import DashboardAlertStrip from "../components/dashboard/DashboardAlertStrip.vue";
import AgentPerformanceTable from "../components/dashboard/AgentPerformanceTable.vue";
import AgentTokenBars from "../components/dashboard/AgentTokenBars.vue";
import RecentUsersCard from "../components/dashboard/RecentUsersCard.vue";
import RecentCallsCard from "../components/dashboard/RecentCallsCard.vue";
import RequestTrendChart from "../components/dashboard/RequestTrendChart.vue";
import DeploymentChecklist from "../components/system/DeploymentChecklist.vue";
import DateRangePicker from "@/components/common/DateRangePicker.vue";
import { formatTokenCompact } from "@/utils/tokenFormat";
import { buildRangePresets, formatDayKey, startOfDay } from "@/utils/dateRange";

const router = useRouter();
const API_BASE = "";

const userInfo = ref<any>(null);
const loading = ref(false);

/**
 * 统计区间：默认今天。
 *
 * 原来只有「今日/本周/本月」三选一，且后端 `week` 其实是滚动 7 天——标签和
 * 口径对不上。改成日历后口径由用户选的日期决定，快捷项里的「本周」是自然周
 * （周一至今），「上周」是上周一至上周日。
 */
const dateRange = ref({ start: formatDayKey(new Date()), end: formatDayKey(new Date()) });

const OVERVIEW_PRESET_KEYS = ["today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"];

const rangeParams = computed(() => ({
  start_date: dateRange.value.start,
  end_date: dateRange.value.end,
}));

const rangePreset = computed(
  () =>
    buildRangePresets(startOfDay(new Date())).find(
      (preset) => preset.start === dateRange.value.start && preset.end === dateRange.value.end,
    ) ?? null,
);

/** 命中快捷项就用它的名字，否则说明是手选区间 */
const rangeName = computed(() => rangePreset.value?.label ?? "自定义区间");
const rangeFoot = computed(() => (rangePreset.value ? `${rangePreset.value.label}累计` : "区间累计"));
const rangeScope = computed(() =>
  rangePreset.value ? rangePreset.value.key : `${dateRange.value.start}~${dateRange.value.end}`,
);

const stats = ref<any>(null);
const agentStats = ref<any>(null);
const activities = ref<any>(null);
const trends24h = ref<any[]>([]);
const agentTokens = ref<any[]>([]);

/** 刷新按钮右侧的下拉：目前只有「部署检查」，管理员可见 */
const refreshMenuOpen = ref(false);
const refreshMenuRef = ref<HTMLElement | null>(null);

/**
 * 部署检查展开。
 *
 * 复用 DeploymentChecklist 已有的全局事件：详情区那个实例监听
 * `deployment-checklist:open` 后会强制展开并滚动过去，这里只需要发事件，
 * 不用把组件的内部状态暴露出来。
 */
const runDeploymentCheck = () => {
  refreshMenuOpen.value = false;
  window.dispatchEvent(new CustomEvent("deployment-checklist:open"));
};

const onDocumentClick = (event: MouseEvent) => {
  if (!refreshMenuOpen.value) return;
  const target = event.target as Node;
  if (refreshMenuRef.value && refreshMenuRef.value.contains(target)) return;
  refreshMenuOpen.value = false;
};

onMounted(() => document.addEventListener("click", onDocumentClick));
onUnmounted(() => document.removeEventListener("click", onDocumentClick));

/** 请求序号：周期切换或并发刷新时丢弃过期响应，避免数据与所选周期不一致 */
let dataSeq = 0;

/**
 * 区段级失败收集：概览由多个独立接口拼装，任何一段失败都不应静默，
 * 但逐条弹 Toast 会刷屏，因此汇总成一条提示。
 */
const sectionErrors = ref<string[]>([]);
const reportSectionError = (section: string) => {
  if (!sectionErrors.value.includes(section)) sectionErrors.value.push(section);
};

let toastSeq = 0;

const toasts = ref<
  Array<{
    id: number;
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
  toasts.value.push({ id: ++toastSeq, message, type, duration });
};

/** 按唯一 id 移除：用数组下标在中间项被移除后会导致后续项错位 */
const removeToast = (id: number) => {
  const index = toasts.value.findIndex((toast) => toast.id === id);
  if (index !== -1) toasts.value.splice(index, 1);
};

/** 汇总本次刷新的区段失败，避免失败被当成"没有数据" */
const flushSectionErrors = () => {
  if (sectionErrors.value.length === 0) return;
  addToast(
    `以下数据加载失败：${sectionErrors.value.join("、")}，可点击刷新重试`,
    "error"
  );
  sectionErrors.value = [];
};

const fetchAgentStats = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/agent-stats`,
      {
        params: rangeParams.value,
      }
    );
    if (seq !== dataSeq) return; // 已有更新的请求，丢弃过期响应
    agentStats.value = response.data;
  } catch (error) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch agent stats:", error);
    reportSectionError("智能体统计");
  }
};

const fetchTrends24h = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/api-trends-24h`
    );
    if (seq !== dataSeq) return;
    trends24h.value = response.data;
  } catch (error: any) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch 24h trends:", error);
    reportSectionError("请求趋势");
  }
};

const fetchAdminStats = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/admin-stats`,
      {
        params: rangeParams.value,
      }
    );
    if (seq !== dataSeq) return;
    stats.value = response.data;
  } catch (error: any) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch admin stats:", error);
    addToast(error.response?.data?.detail || "加载统计数据失败", "error");
  }
};

const fetchUserStats = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/user-stats`,
      {
        params: rangeParams.value,
      }
    );
    if (seq !== dataSeq) return;
    stats.value = response.data;
  } catch (error: any) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch user stats:", error);
    addToast(error.response?.data?.detail || "加载统计数据失败", "error");
  }
};

const fetchRecentActivities = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/recent-activities`,
      {
        params: { limit: 10 },
      }
    );
    if (seq !== dataSeq) return;
    activities.value = response.data;
  } catch (error: any) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch recent activities:", error);
    reportSectionError("最近活动");
  }
};

const fetchAgentTokens = async () => {
  const seq = dataSeq;
  try {
    const response = await axios.get(
      `${API_BASE}/api/portal/dashboard/token-stats/agents`,
      {
        params: rangeParams.value,
      }
    );
    if (seq !== dataSeq) return;
    agentTokens.value = response.data;
  } catch (error) {
    if (seq !== dataSeq) return;
    console.error("Failed to fetch agent token distribution:", error);
    reportSectionError("Token 分布");
  }
};

/** 周期相关数据：周期切换与整体刷新共用 */
const refreshPeriodBoundData = async () => {
  dataSeq += 1;
  if (userInfo.value?.role === "admin") {
    await fetchAdminStats();
  } else {
    await fetchUserStats();
  }
  await Promise.all([fetchAgentStats(), fetchAgentTokens()]);
};

const onRangeChange = async () => {
  sectionErrors.value = [];
  loading.value = true;
  try {
    await refreshPeriodBoundData();
  } finally {
    loading.value = false;
  }
  flushSectionErrors();
};

const refreshData = async () => {
  sectionErrors.value = [];
  loading.value = true;
  try {
    await refreshPeriodBoundData();
    await Promise.all([fetchRecentActivities(), fetchTrends24h()]);
  } finally {
    loading.value = false;
  }
  flushSectionErrors();
};

const goToAgentDebug = (traceId: string) => {
  router.push({
    path: "/dashboard/agent-debug",
    query: { traceId },
  });
};

/** 耗时阈值：超过即判定「偏高」，与趋势图的阈值线共用同一个数 */
const LATENCY_THRESHOLD = 5000;

const apiTotal = computed(() => Number(stats.value?.api_calls?.total || 0));
const apiSuccess = computed(() => Number(stats.value?.api_calls?.success || 0));
const apiFailed = computed(() => Math.max(apiTotal.value - apiSuccess.value, 0));
const successPercent = computed(() => Number(stats.value?.success_rate || 0));

const totalUsers = computed(() => Number(stats.value?.total_users || 0));
const activeUsers = computed(() => Number(stats.value?.active_users || 0));

/** 活跃数现在是总用户的子集，才敢算占比；修复前分子大于分母，比值没有意义 */
const activeUsersFoot = computed(() => {
  if (!totalUsers.value) return "近 7 天无活跃";
  return `活跃 ${activeUsers.value} · 占 ${Math.round((activeUsers.value / totalUsers.value) * 100)}%`;
});

const toolSuccessRate = computed(() =>
  Number(agentStats.value?.health_stats?.success_rate || 0),
);
const toolCallsTotal = computed(() =>
  Number(agentStats.value?.health_stats?.total_tool_calls || 0),
);

/** 工具分布直接取接口顺序，排行组件内部按调用量重排，长度按占总量比例 */
const toolRankingItems = computed(() =>
  (agentStats.value?.tool_usage || []).map((item: any) => ({
    name: String(item?.name ?? ""),
    value: Number(item?.value || 0),
  })),
);

const latencyPoints = computed(() => agentStats.value?.performance_trend || []);
const maxLatency = computed(() =>
  (latencyPoints.value as any[]).reduce(
    (max, point) => Math.max(max, Number(point?.avg_ms || 0)),
    0,
  ),
);

const recentErrors = computed(() => (agentStats.value?.recent_errors || []).slice(0, 3));

/** 失败集中在哪些工具：只说「12 次失败」用户还得自己翻日志 */
const failureSubjects = computed(() => {
  const names = recentErrors.value
    .map((item: any) => item?.tool)
    .filter((name: unknown): name is string => Boolean(name));
  return [...new Set(names)].slice(0, 2).join(" 与 ");
});

const healthKpis = computed(() => {
  const rate = toolSuccessRate.value;
  const avgLatency = Number(agentStats.value?.health_stats?.avg_latency || 0);
  return [
    {
      label: "工具成功率",
      value: rate.toFixed(2),
      unit: "%",
      tone: rate < 95 ? ("danger" as const) : ("ok" as const),
    },
    {
      label: "平均执行耗时",
      value: Math.round(avgLatency).toLocaleString(),
      unit: "ms",
      tone: avgLatency > LATENCY_THRESHOLD ? ("warn" as const) : undefined,
    },
    { label: "工具调用总数", value: toolCallsTotal.value.toLocaleString() },
  ];
});

const agentPerfRows = computed(() =>
  (agentStats.value?.agent_performance || []).map((agent: any) => ({
    name: String(agent?.name ?? agent?.agent_id ?? "未命名智能体"),
    calls: Number(agent?.calls || 0),
    avgLatency: Number(agent?.avg_latency || 0),
    successRate: Number(agent?.success_rate || 0),
  })),
);

const tokenBarItems = computed(() =>
  (agentTokens.value || []).map((item: any) => ({
    name: String(item?.name ?? ""),
    value: Number(item?.total_tokens || 0),
  })),
);

const apiKeyActive = computed(() => stats.value?.api_key_status === "active");
const apiKeyLabel = computed(() => (apiKeyActive.value ? "正常" : "已禁用"));

/** 列表里只显示时分，日期换行反而让一列变高 */
const formatClock = (iso?: string | null) => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
};

/** 异常条的出口：有具体 trace 就直达那一条，否则落到调试页列表 */
const openFailureDetails = () => {
  const firstTraceId = recentErrors.value[0]?.trace_id;
  router.push({
    path: "/dashboard/agent-debug",
    query: firstTraceId ? { traceId: firstTraceId } : {},
  });
};

const loadUserInfo = () => {
  const stored = localStorage.getItem("user_info");
  if (!stored) return;
  try {
    userInfo.value = JSON.parse(stored);
  } catch (error) {
    // 损坏的缓存不应中断整个页面初始化（此前会直接抛出，导致后续数据全部不再加载）
    console.error("解析本地用户信息失败，已清理损坏缓存", error);
    localStorage.removeItem("user_info");
  }
};

onMounted(async () => {
  loadUserInfo();
  await refreshData();
});
</script>

<style scoped>
.ov-card {
  @apply overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-sm;
}
.ov-card-head {
  @apply flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-3.5;
}
.ov-card-title {
  @apply flex items-center text-sm font-bold text-gray-900;
}
.ov-card-body {
  @apply p-5;
}
.ov-dot {
  @apply mr-2 h-1.5 w-1.5 rounded-full bg-primary;
}

</style>
