<template>
  <div class="relative space-y-4 sm:space-y-5">
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
        <h1 class="min-w-0 text-2xl font-bold tracking-normal text-gray-900">我的工作台</h1>
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-600 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="loading"
          :aria-busy="refreshing || loading"
          :title="refreshing || loading ? '刷新中' : '刷新'"
          @click="refresh"
        >
          <svg
            class="h-4 w-4"
            :class="{ 'animate-spin': refreshing || loading }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.6m14.8 2A8 8 0 004.6 9m0 0H9m11 11v-5h-.6m0 0A8 8 0 014.6 13m14.8 2H15"
            />
          </svg>
        </button>
      </div>
      <p class="mt-0.5 truncate text-sm text-gray-500">
        先处理今天值得关注的事情，再继续最近的工作。
      </p>
    </header>

    <div
      v-if="payload && !quietMode"
      class="flex flex-wrap items-center gap-2"
    >
      <span
        class="inline-flex items-center rounded-lg border px-2.5 py-1 text-xs font-medium"
        :class="summaryToneClass"
      >
        {{ summaryPrimary }}
      </span>
      <span
        v-if="summarySecondary"
        class="inline-flex items-center rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-600 shadow-sm"
      >
        {{ summarySecondary }}
      </span>
    </div>

    <div
      v-if="bannerMessage"
      class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700"
    >
      {{ bannerMessage }}
    </div>

    <div v-if="loading && !payload" class="space-y-3">
      <div v-for="index in 3" :key="index" class="h-28 animate-pulse rounded-2xl bg-gray-100" />
    </div>

    <template v-else-if="payload">
      <template v-if="activeMode">
        <WorkbenchAttention
          :items="payload.attention"
          @open-item="openItem"
          @view-all="openTasks"
        />
        <div class="grid gap-4 xl:grid-cols-2">
          <WorkbenchResume
            :items="payload.resume_items"
            @open-item="openItem"
            @view-all="openChat"
          />
          <WorkbenchResults
            :items="payload.latest_results"
            @open-item="openItem"
            @view-all="openReports"
          />
        </div>
        <WorkbenchNextScheduled
          v-if="payload.next_scheduled_item"
          :item="payload.next_scheduled_item"
          @open-item="openItem"
        />
        <WorkbenchAgents
          :agents="payload.favorite_agents"
          @open-agent="openAgent"
          @view-all="openChat"
        />
      </template>

      <template v-else-if="quietMode">
        <div class="rounded-2xl border border-emerald-100 bg-emerald-50/60 px-4 py-3.5">
          <p class="text-sm font-medium text-emerald-800">今日运行正常</p>
          <p class="mt-0.5 text-xs text-emerald-700/80">
            暂无失败任务和待确认事项。{{ summarySecondary }}，可以从下方继续。
          </p>
        </div>
        <div class="grid gap-4 xl:grid-cols-2">
          <WorkbenchResume
            :items="payload.resume_items"
            @open-item="openItem"
            @view-all="openChat"
          />
          <WorkbenchResults
            :items="payload.latest_results"
            @open-item="openItem"
            @view-all="openReports"
          />
        </div>
        <WorkbenchNextScheduled
          v-if="payload.next_scheduled_item"
          :item="payload.next_scheduled_item"
          @open-item="openItem"
        />
        <WorkbenchAgents
          :agents="payload.favorite_agents"
          @open-agent="openAgent"
          @view-all="openChat"
        />
      </template>

      <template v-else-if="newUserMode">
        <section class="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm sm:p-6">
          <p class="text-xs font-medium text-blue-600">开始使用</p>
          <h2 class="mt-1.5 text-lg font-bold text-gray-900">
            {{ failedSources.length ? "工作台部分数据暂时不可用" : "欢迎使用 NanZi" }}
          </h2>
          <p class="mt-1.5 max-w-2xl text-sm text-gray-500">
            {{
              failedSources.length
                ? "你仍可从当前可用的业务助手继续工作。"
                : "选择一个业务场景，或直接找一个助手开始第一项工作。"
            }}
          </p>
          <div class="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              class="rounded-lg bg-blue-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-blue-700"
              @click="openScenarios"
            >
              浏览场景包
            </button>
            <button
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-3.5 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
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
        <WorkbenchAgents
          :agents="payload.favorite_agents"
          @open-agent="openAgent"
          @view-all="openChat"
        />
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from "vue"
import { useRouter } from "vue-router"
import WorkbenchAttention from "@/components/workbench/WorkbenchAttention.vue"
import WorkbenchResults from "@/components/workbench/WorkbenchResults.vue"
import WorkbenchResume from "@/components/workbench/WorkbenchResume.vue"
import WorkbenchAgents from "@/components/workbench/WorkbenchAgents.vue"
import WorkbenchScenarios from "@/components/workbench/WorkbenchScenarios.vue"
import WorkbenchNextScheduled from "@/components/workbench/WorkbenchNextScheduled.vue"
import { useWorkbenchHome } from "@/composables/useWorkbenchHome"
import type { WorkbenchAgent, WorkbenchItem, WorkbenchScenario } from "@/types/workbench"

const router = useRouter()
const { payload, loading, refreshing, error, load, refresh } = useWorkbenchHome()
const activeMode = computed(() => payload.value?.mode === "active")
const quietMode = computed(() => payload.value?.mode === "quiet")
const newUserMode = computed(() => payload.value?.mode === "new_user")
const sourceLabels: Record<string, string> = {
  notifications: "通知",
  tasks: "任务",
  reports: "报表",
  conversations: "会话",
  agents: "助手",
  scenarios: "场景",
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

const summaryPrimary = computed(() => {
  if (!payload.value) return ""
  if (activeMode.value) return `待处理 ${payload.value.attention.length}`
  if (quietMode.value) return "今日运行正常"
  return "开始第一项工作"
})

const summarySecondary = computed(() => {
  if (!payload.value) return ""
  if (activeMode.value) {
    return `最新结果 ${payload.value.latest_results.length} · 可继续 ${payload.value.resume_items.length}`
  }
  if (quietMode.value) {
    return `可继续 ${payload.value.resume_items.length} · 最新结果 ${payload.value.latest_results.length}`
  }
  return `推荐场景 ${payload.value.recommended_scenarios.length} · 可用助手 ${payload.value.favorite_agents.length}`
})

const summaryToneClass = computed(() => {
  if (activeMode.value) return "border-amber-200 bg-amber-50 text-amber-800"
  if (quietMode.value) return "border-emerald-200 bg-emerald-50 text-emerald-800"
  return "border-blue-200 bg-blue-50 text-blue-800"
})

function openItem(item: WorkbenchItem) {
  const target = item.target || {}
  if (item.action === "open_task_log" || item.action === "open_task") {
    router.push({
      path: "/dashboard/tasks",
      query: {
        task_id: target.task_id,
        ...(target.run_id != null ? { run_id: target.run_id } : {}),
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
  router.push({ path: "/dashboard/tasks" })
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
