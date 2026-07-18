<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ChevronRightIcon,
  SparklesIcon,
} from '@heroicons/vue/24/outline'
import { portalApi } from '../api/portal'
import type { ScenarioTemplateInstanceSummary, ScenarioTemplateSummary } from '../api/portal'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'

type MarketTab = 'templates' | 'delivered'

const router = useRouter()
const { showToast } = useToast()
const { hasPermission } = useUser()
const canManageDelivery = computed(() => hasPermission('menu:agent_management'))

const templates = ref<ScenarioTemplateSummary[]>([])
const installedInstances = ref<ScenarioTemplateInstanceSummary[]>([])
const loading = ref(false)
const selectedCategory = ref('全部')
const activeTab = ref<MarketTab>('templates')

const categoryOptions = computed(() => {
  const base = ['全部', '数据分析', '知识问答', '运维自动化']
  const dynamic = templates.value.map((template) => template.category).filter(Boolean)
  return Array.from(new Set([...base, ...dynamic]))
})

const filteredTemplates = computed(() => {
  if (selectedCategory.value === '全部') return templates.value
  if (selectedCategory.value === '运维自动化') {
    return templates.value.filter((template) => template.category === '运维')
  }
  return templates.value.filter((template) => template.category === selectedCategory.value)
})

const templateStats = computed(() => ({
  total: templates.value.length,
  delivered: installedInstances.value.length,
  recommended: templates.value.filter((template) => template.recommended).length,
}))

const loadTemplates = async () => {
  loading.value = true
  try {
    const res = await portalApi.getScenarioTemplates()
    templates.value = res.data.data || []
  } catch (error) {
    showToast('获取场景模板失败', 'error')
  } finally {
    loading.value = false
  }
}

const loadInstalledInstances = async () => {
  if (!canManageDelivery.value) {
    installedInstances.value = []
    return
  }
  try {
    const res = await portalApi.getScenarioTemplateInstances()
    installedInstances.value = res.data.data || []
  } catch (error) {
    showToast('获取已交付场景失败', 'error')
  }
}

const openDetail = (template: ScenarioTemplateSummary) => {
  router.push({ name: 'ScenarioTemplateDetail', params: { templateId: template.id } })
}

const openInstalledDebug = (instance: ScenarioTemplateInstanceSummary) => {
  router.push({
    name: 'AgentDebug',
    query: {
      agent_id: instance.agent.id,
      version_id: instance.latest_run?.version_id || undefined,
      sample_question: instance.sample_questions[0] || undefined,
    },
  })
}

const resourceSummary = (template: ScenarioTemplateSummary) => {
  const required = template.required_resources.filter((item) => item.required)
  const optional = template.required_resources.filter((item) => !item.required)
  return {
    required: required.map((item) => item.name).join('、') || '无必选资源',
    optional: optional.map((item) => item.name).join('、') || '无建议资源',
  }
}

const categoryTone = (category: string) => {
  if (category === 'ChatBI' || category === '数据分析') return 'bg-blue-50 text-blue-700 border-blue-100'
  if (category === '知识库' || category === '知识问答') return 'bg-violet-50 text-violet-700 border-violet-100'
  if (category === '运维') return 'bg-amber-50 text-amber-700 border-amber-100'
  return 'bg-gray-50 text-gray-600 border-gray-100'
}

onMounted(async () => {
  await Promise.all([loadTemplates(), loadInstalledInstances()])
})
</script>

<template>
  <div class="space-y-4 sm:space-y-5">
    <section class="overflow-hidden rounded-2xl border border-blue-800/30 bg-blue-800 p-6 text-white shadow-sm">
      <div class="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
        <div class="max-w-3xl">
          <p class="text-xs font-semibold uppercase tracking-[0.24em] text-blue-100">Solution Package</p>
          <h1 class="mt-2 text-3xl font-bold text-white">场景包市场</h1>
          <p class="mt-3 text-sm font-medium leading-6 text-blue-50">
            按业务场景一键交付智能体：从方案说明、资源绑定、预检安装到调试验收，形成可复用的交付闭环。
          </p>
          <div class="mt-5 flex flex-wrap gap-2">
            <span class="rounded-full bg-white px-3 py-1 text-xs font-semibold text-blue-800 shadow-sm">数据分析</span>
            <span class="rounded-full bg-white px-3 py-1 text-xs font-semibold text-blue-800 shadow-sm">知识问答</span>
            <span class="rounded-full bg-white px-3 py-1 text-xs font-semibold text-blue-800 shadow-sm">运维自动化</span>
          </div>
        </div>
        <div class="grid min-w-[280px] grid-cols-3 gap-3">
          <div class="rounded-xl bg-white p-4 text-center shadow-sm">
            <div class="text-2xl font-bold text-blue-900">{{ templateStats.total }}</div>
            <div class="mt-1 text-xs font-medium text-blue-700">模板总数</div>
          </div>
          <div class="rounded-xl bg-white p-4 text-center shadow-sm">
            <div class="text-2xl font-bold text-blue-900">{{ templateStats.delivered }}</div>
            <div class="mt-1 text-xs font-medium text-blue-700">已交付</div>
          </div>
          <div class="rounded-xl bg-white p-4 text-center shadow-sm">
            <div class="text-2xl font-bold text-blue-900">{{ templateStats.recommended }}</div>
            <div class="mt-1 text-xs font-medium text-blue-700">推荐场景</div>
          </div>
        </div>
      </div>
    </section>

    <section class="rounded-xl border border-gray-200 bg-white p-2 shadow-sm">
      <div class="grid gap-2" :class="canManageDelivery ? 'sm:grid-cols-2' : 'sm:grid-cols-1'">
        <button
          class="rounded-lg px-4 py-3 text-left transition"
          :class="activeTab === 'templates' ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-100' : 'text-gray-500 hover:bg-gray-50'"
          @click="activeTab = 'templates'"
        >
          <span class="block text-sm font-bold">可交付模板</span>
          <span class="mt-1 block text-xs">按业务场景选择模板，进入详情和交付向导。</span>
        </button>
        <button
          v-if="canManageDelivery"
          class="rounded-lg px-4 py-3 text-left transition"
          :class="activeTab === 'delivered' ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-100' : 'text-gray-500 hover:bg-gray-50'"
          @click="activeTab = 'delivered'"
        >
          <span class="block text-sm font-bold">已交付场景</span>
          <span class="mt-1 block text-xs">查看已安装实例、交付记录，并直接打开调试台。</span>
        </button>
      </div>
    </section>

    <section v-if="activeTab === 'delivered'" class="rounded-lg border border-blue-100 bg-white p-5 shadow-sm">
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-xs font-semibold uppercase text-blue-600">Delivered</p>
          <h2 class="mt-1 text-lg font-bold text-gray-900">已交付场景</h2>
          <p class="mt-1 text-sm text-gray-500">这里可以继续查看已安装场景，并直接带着推荐问题打开调试台验收。</p>
        </div>
      </div>
      <div v-if="installedInstances.length" class="mt-4 grid gap-3 xl:grid-cols-2">
        <article
          v-for="instance in installedInstances"
          :key="instance.id"
          class="rounded-lg border border-gray-200 bg-gray-50 p-4"
        >
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-sm font-semibold text-gray-900">{{ instance.agent.display_name }}</h3>
              <p class="mt-1 text-xs text-gray-500">{{ instance.template_name }} · {{ instance.agent.name }}</p>
            </div>
            <span class="rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">{{ instance.status }}</span>
          </div>
          <div class="mt-3 space-y-1 text-xs text-gray-600">
            <div v-for="resource in instance.resource_summary" :key="resource.type" class="flex justify-between gap-3">
              <span>{{ resource.label }}</span>
              <span class="text-right text-gray-500">{{ (resource.names?.length ? resource.names : resource.ids).join('、') }}</span>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              class="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
              @click="openInstalledDebug(instance)"
            >
              打开调试台
            </button>
            <button
              class="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
              @click="router.push({ name: 'ScenarioTemplateInstall', params: { templateId: instance.template_id }, query: { step: 'done', instance_id: instance.id } })"
            >
              查看交付记录
            </button>
          </div>
        </article>
      </div>
      <div v-else class="mt-4 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center text-sm text-gray-500">
        暂无已交付场景。可以先切换到“可交付模板”选择一个场景完成安装。
      </div>
    </section>

    <section v-if="activeTab === 'templates'" class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 class="text-lg font-bold text-gray-900">可交付场景模板</h2>
          <p class="mt-1 text-sm text-gray-500">选择最接近业务目标的模板，再进入详情页查看交付物、资源要求和验收标准。</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="category in categoryOptions"
            :key="category"
            class="rounded-full border px-3 py-1.5 text-xs font-medium transition"
            :class="selectedCategory === category ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-500 hover:border-blue-200 hover:text-blue-600'"
            @click="selectedCategory = category"
          >
            {{ category }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="activeTab === 'templates'" class="grid gap-4 xl:grid-cols-4 lg:grid-cols-3 md:grid-cols-2">
      <article
        v-for="template in filteredTemplates"
        :key="template.id"
        class="group flex min-h-[360px] flex-col rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded border px-2 py-0.5 text-xs font-medium" :class="categoryTone(template.category)">{{ template.category }}</span>
              <span v-if="template.recommended" class="rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">推荐</span>
            </div>
            <h2 class="mt-3 text-lg font-semibold text-gray-900">{{ template.name }}</h2>
            <p class="mt-2 text-sm leading-6 text-gray-600">{{ template.description }}</p>
          </div>
          <SparklesIcon class="h-5 w-5 flex-shrink-0 text-blue-500" />
        </div>

        <div class="mt-5 grid grid-cols-2 gap-2 text-xs">
          <div class="rounded-md bg-gray-50 p-3">
            <div class="text-gray-400">预计交付</div>
            <div class="mt-1 font-semibold text-gray-800">{{ template.delivery_time || '待评估' }}</div>
          </div>
          <div class="rounded-md bg-gray-50 p-3">
            <div class="text-gray-400">交付成熟度</div>
            <div class="mt-1 font-semibold text-gray-800">{{ template.maturity || '基础版' }}</div>
          </div>
        </div>

        <div class="mt-4">
          <div class="text-xs font-semibold text-gray-400">资源要求</div>
          <div class="mt-2 rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs leading-5 text-gray-600">
            <div><span class="font-medium text-gray-800">必选：</span>{{ resourceSummary(template).required }}</div>
            <div><span class="font-medium text-gray-800">建议：</span>{{ resourceSummary(template).optional }}</div>
          </div>
        </div>

        <div class="mt-4">
          <div class="text-xs font-semibold text-gray-400">适用部门</div>
          <div class="mt-2 flex flex-wrap gap-1.5">
            <span
              v-for="dept in template.target_departments"
              :key="dept"
              class="rounded border border-gray-200 px-2 py-0.5 text-xs text-gray-600"
            >
              {{ dept }}
            </span>
          </div>
        </div>

        <div class="mt-4 flex flex-wrap gap-2">
          <span
            v-for="capability in template.included_capabilities"
            :key="capability"
            class="rounded-md border border-gray-200 bg-gray-50 px-2 py-1 text-xs text-gray-600"
          >
            {{ capability }}
          </span>
        </div>

        <button
          class="mt-auto inline-flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          @click="openDetail(template)"
        >
          查看方案
          <ChevronRightIcon class="h-4 w-4" />
        </button>
      </article>
    </div>

    <div v-if="!loading && !templates.length" class="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
      暂无可用场景模板
    </div>
  </div>
</template>
