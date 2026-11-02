<template>
  <div class="relative space-y-3 sm:space-y-4">
    <!-- 进度条始终占位，用透明度切换，避免出现/消失时视觉跳动 -->
    <div
      class="pointer-events-none absolute inset-x-0 top-0 h-0.5 overflow-hidden rounded-full transition-opacity duration-150"
      :class="refreshing ? 'opacity-100' : 'opacity-0'"
      aria-hidden="true"
    >
      <div
        class="h-full w-1/3 rounded-full bg-blue-500"
        :class="refreshing ? 'animate-pulse' : ''"
      />
    </div>

    <header>
      <div class="flex items-center justify-between gap-3">
        <h1 class="min-w-0 text-2xl font-bold tracking-normal text-gray-900 dark:text-gray-100">我的工作台</h1>
        <button
          type="button"
          class="workbench-refresh-btn inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-gray-500 transition-colors hover:bg-blue-50 hover:text-blue-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-blue-400"
          :disabled="busy"
          :aria-busy="busy"
          :aria-label="
            busy
              ? '刷新中'
              : activeTab === 'square'
                ? '刷新资源广场'
                : activeTab === 'gallery'
                  ? '刷新技能广场'
                  : '刷新工作台'
          "
          @click="refreshCurrent"
        >
          <svg
            class="h-3.5 w-3.5"
            :class="{ 'animate-spin': busy }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.6m14.8 2A8 8 0 004.6 9m0 0H9m11 11v-5h-.6m0 0A8 8 0 014.6 13m14.8 2H15"
            />
          </svg>
          <span>{{ busy ? '刷新中' : '刷新' }}</span>
        </button>
      </div>
      <p class="mt-0.5 truncate text-sm text-gray-500 dark:text-gray-400">
        先处理今天值得关注的事情，再继续最近的工作。
      </p>
    </header>

    <nav class="flex gap-6 border-b border-gray-100 dark:border-gray-800" aria-label="工作台视图">
      <button
        type="button"
        class="-mb-px border-b-2 pb-2.5 text-sm font-medium transition-colors"
        :class="activeTab === 'workbench' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400'"
        @click="switchTab('workbench')"
      >
        工作台
      </button>
      <button
        type="button"
        class="-mb-px border-b-2 pb-2.5 text-sm font-medium transition-colors"
        :class="activeTab === 'square' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400'"
        @click="switchTab('square')"
      >
        资源广场
      </button>
      <button
        type="button"
        class="-mb-px border-b-2 pb-2.5 text-sm font-medium transition-colors"
        :class="activeTab === 'gallery' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400'"
        @click="switchTab('gallery')"
      >
        技能广场
      </button>
    </nav>

    <div
      v-if="bannerMessage"
      class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-300"
    >
      {{ bannerMessage }}
    </div>

    <div v-if="activeTab === 'workbench'">
      <div
        v-if="loading && !payload"
        class="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_336px]"
      >
        <div class="space-y-3">
          <div v-for="index in 3" :key="index" class="h-24 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800/50" />
        </div>
        <div class="hidden space-y-3 xl:block">
          <div v-for="index in 2" :key="index" class="h-40 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800/50" />
        </div>
      </div>

      <div
        v-else-if="payload"
        class="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_336px]"
      >
        <div class="flex min-w-0 flex-col gap-5">
          <!-- 安静态：没有待处理事项是好消息，用一行轻提示而不是留一块空卡片 -->
          <div
            v-if="quietMode"
            class="flex items-center gap-2.5 rounded-xl border border-gray-200/80 bg-white px-3.5 py-3 shadow-xs dark:border-gray-800 dark:bg-gray-900/40"
          >
            <span
              class="flex h-[21px] w-[21px] shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-950/50 dark:text-emerald-400"
              aria-hidden="true"
            >
              <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <span class="text-[13px] font-semibold text-gray-900 dark:text-gray-100">今天没有待处理事项</span>
            <span class="text-xs text-gray-400 dark:text-gray-500">有新的进展会在这里提示</span>
          </div>

          <WorkbenchAttention
            v-if="payload.attention.length"
            :items="payload.attention"
            @open-item="openItem"
            @view-all="openTasks"
          />

          <!-- 运行中按「有没有在跑」显示，不跟模式绑死：安静态同样可能有任务在运行 -->
          <WorkbenchRunning
            v-if="payload.running_items.length"
            :items="payload.running_items"
            @open-item="openItem"
          />

          <WorkbenchNextScheduled
            v-if="payload.next_scheduled_item"
            :item="payload.next_scheduled_item"
            @open-item="openItem"
          />

          <!-- 新用户：整页没有可展示的历史时，给的是「从哪开始」而不是四块空卡片 -->
          <template v-if="newUserMode">
            <section
              class="rounded-xl border border-gray-200/80 bg-white px-6 py-8 text-center shadow-xs dark:border-gray-800 dark:bg-gray-900/40"
            >
              <span
                class="mx-auto mb-3 flex h-[46px] w-[46px] items-center justify-center rounded-[14px] bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400"
                aria-hidden="true"
              >
                <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 3c.6 3.6 2.4 5.4 6 6-3.6.6-5.4 2.4-6 6-.6-3.6-2.4-5.4-6-6 3.6-.6 5.4-2.4 6-6Z" />
                  <path d="M19 15c.3 1.8 1.2 2.7 3 3-1.8.3-2.7 1.2-3 3-.3-1.8-1.2-2.7-3-3 1.8-.3 2.7-1.2 3-3Z" />
                </svg>
              </span>
              <h2 class="text-[17px] font-bold tracking-tight text-gray-900 dark:text-gray-100">
                {{
                  failedSources.length
                    ? "工作台部分数据暂时不可用"
                    : `欢迎使用 ${branding.product_name || "NanZi AI Agent"}`
                }}
              </h2>
              <p class="mt-1.5 text-[13px] text-gray-500 dark:text-gray-400">
                {{
                  failedSources.length
                    ? "你仍可从当前可用的业务助手继续工作。"
                    : "选择一个业务场景，或直接找一个助手开始第一项工作。"
                }}
              </p>
              <div class="mt-4 flex flex-wrap justify-center gap-2">
                <button
                  type="button"
                  class="rounded-lg bg-gray-900 px-3.5 py-1.5 text-[13px] font-semibold text-white hover:bg-gray-800 active:scale-95 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
                  @click="openScenarios"
                >
                  浏览场景包
                </button>
                <button
                  type="button"
                  class="rounded-lg border border-gray-200 bg-white px-3.5 py-1.5 text-[13px] font-semibold text-gray-700 hover:border-blue-200 hover:text-blue-600 active:scale-95 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                  @click="openChat"
                >
                  打开智能助手
                </button>
              </div>
            </section>

            <WorkbenchScenarios
              :scenarios="payload.recommended_scenarios"
              :agents-available="payload.favorite_agents.length > 0"
              @open-scenario="openScenario"
              @view-all="openScenarios"
            />
          </template>

          <WorkbenchFeed
            v-if="feedItems.length"
            :items="feedItems"
            @open-item="openItem"
            @view-all="onFeedViewAll"
          />
        </div>

        <aside class="flex min-w-0 flex-col gap-4 xl:sticky xl:top-4">
          <WorkbenchResourceGrid
            v-if="workbenchPersonalResources.length"
            :items="workbenchPersonalResources"
            @select="openPersonalResource"
          />
          <WorkbenchAgents
            :agents="payload.favorite_agents"
            @open-agent="openAgent"
            @view-all="openChat"
          />
        </aside>
      </div>
    </div>

    <ResourceSquarePanel v-if="activeTab === 'square'" ref="squarePanel" />
    <SkillGalleryPanel v-if="activeTab === 'gallery'" ref="galleryPanel" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import WorkbenchAttention from "@/components/workbench/WorkbenchAttention.vue"
import WorkbenchFeed from "@/components/workbench/WorkbenchFeed.vue"
import WorkbenchAgents from "@/components/workbench/WorkbenchAgents.vue"
import WorkbenchScenarios from "@/components/workbench/WorkbenchScenarios.vue"
import WorkbenchNextScheduled from "@/components/workbench/WorkbenchNextScheduled.vue"
import WorkbenchRunning from "@/components/workbench/WorkbenchRunning.vue"
import WorkbenchResourceGrid from "@/components/workbench/WorkbenchResourceGrid.vue"
import ResourceSquarePanel from "@/components/resource-square/ResourceSquarePanel.vue"
import SkillGalleryPanel from "@/components/skill-gallery/SkillGalleryPanel.vue"

import { useWorkbenchHome } from "@/composables/useWorkbenchHome"
import { useBranding } from "@/composables/useBranding"
import {
  filterWorkbenchPersonalResources,
  isInboxPersonalResource,
  openPortalInboxPanel,
} from "@/constants/personalResources"
import type {
  WorkbenchAgent,
  WorkbenchItem,
  WorkbenchPersonalResource,
  WorkbenchScenario,
} from "@/types/workbench"

const router = useRouter()
const route = useRoute()

type WorkbenchTab = "workbench" | "square" | "gallery"

const TAB_QUERY_VALUES: readonly WorkbenchTab[] = ["square", "gallery"]

function tabFromQuery(value: unknown): WorkbenchTab {
  const raw = Array.isArray(value) ? value[0] : value
  return TAB_QUERY_VALUES.includes(raw as WorkbenchTab) ? (raw as WorkbenchTab) : "workbench"
}

// tab 状态与 URL 同步：从「我的知识库」卡片跳过来时直接落在资源广场，
// 刷新页面也不该跳回工作台；技能广场同理（?tab=gallery）
const activeTab = ref<WorkbenchTab>(tabFromQuery(route.query.tab))

function switchTab(next: WorkbenchTab) {
  activeTab.value = next
  const query = { ...route.query }
  if (next === "workbench") {
    delete query.tab
    delete query.section
    // status 是资源广场的状态筛选，工作台没有这个概念。留着下次进广场
    // 还是「已授权」，用户会以为筛选按钮点了没反应。
    delete query.status
  } else {
    query.tab = next
    // section 只在资源广场里有意义（锚到知识库/数据集），进技能广场要清掉，
    // 否则从广场切过去时地址栏还挂在上一个小节。
    if (next === "gallery") {
      delete query.section
      delete query.status
    }
  }
  router.replace({ query })
}

// 从资源卡跳过来时还在同一个路由上，组件会被复用、setup 不会重跑：
// 只认初始值的话，URL 变了界面却纹丝不动
watch(
  () => route.query.tab,
  (tab) => {
    activeTab.value = tabFromQuery(tab)
  },
)

const { branding } = useBranding()
const { payload, loading, refreshing, error, load, refresh } = useWorkbenchHome()

/**
 * 顶栏「刷新」在资源广场 tab 下也要真的刷新。
 *
 * 工作台数据由 useWorkbenchHome 管，广场的知识库/数据集两段是面板自己 onMounted
 * 拉的——只调 refresh()，站在广场点刷新就是按钮转一圈、列表纹丝不动。
 */
const squarePanel = ref<InstanceType<typeof ResourceSquarePanel> | null>(null)
const squareRefreshing = ref(false)

// 技能广场同样自己拉数据（目录接口），顶栏刷新要一起带上它，
// 否则站在技能广场点刷新又是一次「按钮转一圈、列表纹丝不动」。
const galleryPanel = ref<InstanceType<typeof SkillGalleryPanel> | null>(null)
const galleryRefreshing = ref(false)

const busy = computed(
  () => refreshing.value || loading.value || squareRefreshing.value || galleryRefreshing.value,
)

async function refreshCurrent() {
  if (activeTab.value === 'gallery') {
    galleryRefreshing.value = true
    try {
      // 工作台那份也顺手刷新：切回去时不该是旧数据
      await Promise.all([refresh(), galleryPanel.value?.reloadAll() ?? Promise.resolve()])
    } finally {
      galleryRefreshing.value = false
    }
    return
  }
  if (activeTab.value !== 'square') {
    await refresh()
    return
  }
  squareRefreshing.value = true
  try {
    await Promise.all([refresh(), squarePanel.value?.reloadAll() ?? Promise.resolve()])
  } finally {
    squareRefreshing.value = false
  }
}

const workbenchPersonalResources = computed(() =>
  filterWorkbenchPersonalResources(payload.value?.personal_resources || []),
)
const quietMode = computed(() => payload.value?.mode === "quiet")
const newUserMode = computed(() => payload.value?.mode === "new_user")

/**
 * 三类活动合并成一条时间线。
 *
 * 会话 / 产出 / 任务原来各占一张卡片，三张卡加起来近 500px 只装了 9 条记录，
 * 中间还夹着三块虚线占位。它们本来就都带 occurred_at，按时间排一遍即可。
 */
const feedItems = computed<WorkbenchItem[]>(() => {
  const data = payload.value
  if (!data) return []
  return [...data.resume_items, ...data.latest_results, ...data.recent_tasks].sort(
    (a, b) => timestampOf(b.occurred_at) - timestampOf(a.occurred_at),
  )
})

function timestampOf(value?: string | null): number {
  if (!value) return 0
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

/** 活动流底部入口跟着当前筛选走：三类活动本来落在不同页面 */
function onFeedViewAll(filter: string) {
  if (filter === "report") {
    openReports()
    return
  }
  if (filter === "task") {
    openMyTasks()
    return
  }
  openChat()
}

const sourceLabels: Record<string, string> = {
  notifications: "通知",
  tasks: "任务",
  reports: "报表",
  conversations: "会话",
  agents: "助手",
  scenarios: "场景",
  running: "运行态",
}
const failedSources = computed(() =>
  Object.entries(payload.value?.source_status || {})
    .filter(([, status]) => status === "error")
    .map(([name]) => sourceLabels[name] || name)
)

const bannerMessage = computed(() => {
  const parts: string[] = []
  if (error.value) parts.push(error.value)
  if (failedSources.value.length) {
    parts.push(`部分数据暂时无法获取（${failedSources.value.join("、")}），其他区域仍可正常使用。`)
  }
  return parts.join(" ")
})

function openPersonalResource(item: WorkbenchPersonalResource) {
  // 知识库/数据集不是「个人中心里的一个 tab」，而是广场里的一个分段
  if (item.key === "knowledge" || item.key === "datasets") {
    router.push({
      path: "/dashboard/workbench",
      query: {
        tab: "square",
        section: item.key === "knowledge" ? "knowledge" : "datasets",
        // 卡片上写的是「我有权限的 N 个」，落到广场必须默认只看已授权，
        // 否则广场显示全部，两边数字对不上
        status: "has",
      },
    })
    return
  }
  if (isInboxPersonalResource(item)) {
    openPortalInboxPanel()
    return
  }
  router.push({ path: "/dashboard/personal", query: { tab: item.tab } })
}

function openItem(item: WorkbenchItem) {
  const target = item.target || {}
  if (
    item.action === "open_task_log" ||
    item.action === "open_task" ||
    item.action === "open_task_run"
  ) {
    router.push({
      path: "/dashboard/personal",
      query: {
        tab: "tasks",
        view: item.action === "open_task_run" ? "history" : "tasks",
        ...(target.task_id != null ? { task_id: String(target.task_id) } : {}),
        ...(target.run_id != null ? { run_id: String(target.run_id) } : {}),
        ...(target.trace_id ? { trace_id: String(target.trace_id) } : {}),
      },
    })
  } else if (item.action === "open_digest") {
    router.push({
      path: "/dashboard/chat",
      query: {
        dataset_portal: "1",
        report_id: target.report_id,
        run_id: target.run_id,
        delivery: "1",
      },
    })
  } else if (item.action === "open_report") {
    router.push({
      path: "/dashboard/chat",
      query: {
        dataset_portal: "1",
        report_id: target.report_id,
        run_id: target.run_id,
      },
    })
  } else if (item.action === "open_conversation") {
    router.push({
      path: "/dashboard/chat",
      query: { conversation_id: target.conversation_id },
    })
  }
}

function openAgent(agent: WorkbenchAgent) {
  if (agent.action === "open_agent") {
    router.push({ path: "/dashboard/chat", query: { agent_id: agent.target.agent_id } })
  }
}

function openScenario(scenario: WorkbenchScenario) {
  if (scenario.action === "open_scenario") {
    router.push({
      path: `/dashboard/scenario-templates/${scenario.target.scenario_id || scenario.id}`,
    })
  }
}

function openTasks() {
  router.push({ path: "/dashboard/personal", query: { tab: "tasks" } })
}

function openMyTasks() {
  router.push({ path: "/dashboard/personal", query: { tab: "tasks", view: "history" } })
}

function openReports() {
  router.push({ path: "/dashboard/chat", query: { dataset_portal: "1" } })
}

function openChat() {
  router.push({ path: "/dashboard/chat" })
}

function openScenarios() {
  router.push({ path: "/dashboard/scenario-templates" })
}

onMounted(load)
</script>
