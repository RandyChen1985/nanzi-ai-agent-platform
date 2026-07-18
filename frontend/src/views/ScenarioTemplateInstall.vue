<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeftIcon,
  BeakerIcon,
  CheckCircleIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  RocketLaunchIcon,
  XCircleIcon,
} from '@heroicons/vue/24/outline'
import { portalApi } from '../api/portal'
import type {
  ScenarioTemplateDetail,
  ScenarioTemplateInstallResult,
  ScenarioTemplatePrecheck,
  ScenarioTemplateResourceOption,
} from '../api/portal'
import { useToast } from '../composables/useToast'

type StepId = 'info' | 'resources' | 'install' | 'done'

const route = useRoute()
const router = useRouter()
const { showToast } = useToast()

const steps: Array<{ id: StepId; title: string; hint: string }> = [
  { id: 'info', title: '填写信息', hint: '确认实例名称、显示名称和负责人' },
  { id: 'resources', title: '绑定资源', hint: '确认要补齐的数据集、知识库、工具和通知渠道' },
  { id: 'install', title: '预检安装', hint: '先预检，再一键安装发布' },
  { id: 'done', title: '完成交付', hint: '查看交付清单和下一步动作' },
]

const template = ref<ScenarioTemplateDetail | null>(null)
const currentStepIndex = ref(0)
const loading = ref(false)
const prechecking = ref(false)
const installing = ref(false)
const precheckResult = ref<ScenarioTemplatePrecheck | null>(null)
const installResult = ref<ScenarioTemplateInstallResult | null>(null)
const resourceOptions = ref<Record<string, ScenarioTemplateResourceOption[]>>({})
const resourceBindings = ref<Record<string, string[]>>({})

const form = ref({
  instance_name: '',
  display_name: '',
  description: '',
  owner: '',
})

const currentStep = computed(() => steps[currentStepIndex.value])
const canInstall = computed(() => Boolean(form.value.instance_name.trim() && form.value.display_name.trim()))
const requiredMissing = computed(() => {
  if (!template.value) return []
  return template.value.required_resources.filter((resource) => {
    if (!resource.required) return false
    return !hasResourceBinding(resource.type)
  })
})
const canGoNext = computed(() => {
  if (currentStep.value.id === 'info') return canInstall.value
  if (currentStep.value.id === 'resources') return requiredMissing.value.length === 0
  if (currentStep.value.id === 'install') return Boolean(installResult.value)
  return false
})

const stepFromQuery = () => {
  const raw = String(route.query.step || 'info')
  const index = steps.findIndex((step) => step.id === raw)
  return index >= 0 ? index : 0
}

const syncStepToRoute = () => {
  const step = steps[currentStepIndex.value]
  if (route.query.step === step.id) return
  router.replace({
    name: 'ScenarioTemplateInstall',
    params: route.params,
    query: { ...route.query, step: step.id },
  })
}

const resetFormForTemplate = (item: ScenarioTemplateDetail) => {
  form.value = {
    instance_name: item.id,
    display_name: item.name,
    description: item.description,
    owner: '',
  }
  resourceBindings.value = Object.fromEntries(
    item.required_resources.map((resource) => [resource.type, []])
  )
}

const loadTemplate = async () => {
  const templateId = String(route.params.templateId || '')
  if (!templateId) return
  loading.value = true
  try {
    const res = await portalApi.getScenarioTemplate(templateId)
    template.value = res.data.data || null
    if (template.value) resetFormForTemplate(template.value)
  } catch (error) {
    showToast('获取模板详情失败', 'error')
  } finally {
    loading.value = false
  }
}

const loadResourceOptions = async () => {
  const templateId = String(route.params.templateId || '')
  if (!templateId) return
  try {
    const res = await portalApi.getScenarioTemplateResourceOptions(templateId)
    resourceOptions.value = res.data.data?.options || {}
  } catch (error) {
    showToast('获取可绑定资源失败', 'error')
  }
}

const loadInstalledInstanceResult = async () => {
  const instanceId = String(route.query.instance_id || '')
  if (!instanceId) return
  try {
    const res = await portalApi.getScenarioTemplateInstance(instanceId)
    installResult.value = res.data.data || null
  } catch (error) {
    showToast('获取交付记录失败', 'error')
  }
}

const goStep = (index: number) => {
  const targetIndex = Math.min(Math.max(index, 0), steps.length - 1)
  if (targetIndex > currentStepIndex.value) {
    if (currentStep.value.id === 'info' && !canInstall.value) {
      showToast('请先填写实例标识和显示名称', 'warning')
      return
    }
    if (currentStep.value.id === 'resources' && requiredMissing.value.length > 0) {
      showToast(`请先绑定必选资源：${requiredMissing.value.map((item) => item.name).join('、')}`, 'warning')
      return
    }
    if (steps[targetIndex].id === 'done' && !installResult.value) {
      showToast('请先完成预检安装', 'warning')
      return
    }
  }
  currentStepIndex.value = targetIndex
  syncStepToRoute()
}

const normalizeCurrentStep = () => {
  if (currentStep.value.id === 'done' && !installResult.value) {
    currentStepIndex.value = steps.findIndex((step) => step.id === 'install')
    syncStepToRoute()
  }
}

const goNext = () => goStep(currentStepIndex.value + 1)
const goPrev = () => goStep(currentStepIndex.value - 1)

const buildPayload = () => ({
  instance_name: form.value.instance_name.trim(),
  display_name: form.value.display_name.trim(),
  description: form.value.description.trim() || undefined,
  publish: true,
  resource_bindings: {
    owner: form.value.owner.trim() || undefined,
    ...Object.fromEntries(
      Object.entries(resourceBindings.value)
        .map(([key, values]) => [key, values.filter(Boolean)])
        .filter(([, values]) => Array.isArray(values) && values.length > 0)
    ),
  },
})

const resourceTypeLabel = (type: string) => {
  const labels: Record<string, string> = {
    metadata_dataset: '元数据集',
    knowledge_base: '知识库',
    mcp_tool: 'MCP 工具',
    api_tool: 'API 工具',
    notification: '通知渠道',
    feedback: '反馈能力',
  }
  return labels[type] || type
}

const optionsForResource = (type: string) => resourceOptions.value[type] || []

const hasResourceBinding = (type: string) => {
  const values = resourceBindings.value[type] || []
  return values.filter(Boolean).length > 0
}

const toggleResourceBinding = (type: string, optionId: string) => {
  const current = resourceBindings.value[type] || []
  resourceBindings.value[type] = current.includes(optionId)
    ? current.filter((item) => item !== optionId)
    : [...current, optionId]
  precheckResult.value = null
}

const runPrecheck = async () => {
  if (!template.value || !canInstall.value) return
  prechecking.value = true
  installResult.value = null
  try {
    const res = await portalApi.precheckScenarioTemplate(template.value.id, buildPayload())
    precheckResult.value = res.data.data || null
    showToast('预检完成', 'success')
  } catch (error: any) {
    const msg = error?.response?.data?.detail || '预检失败'
    showToast(msg, 'error')
  } finally {
    prechecking.value = false
  }
}

const installTemplate = async () => {
  if (!template.value || !canInstall.value) return
  installing.value = true
  try {
    const res = await portalApi.installScenarioTemplate(template.value.id, buildPayload())
    installResult.value = res.data.data || null
    showToast(installResult.value?.created ? '场景已安装' : '已复用现有场景', 'success')
    goStep(steps.findIndex((step) => step.id === 'done'))
  } catch (error: any) {
    const msg = error?.response?.data?.detail || '安装失败'
    showToast(msg, 'error')
  } finally {
    installing.value = false
  }
}

const statusClass = (status: string) => {
  if (status === 'success') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (status === 'warning') return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-rose-50 text-rose-700 border-rose-200'
}

const openAgentCenter = () => {
  router.push('/dashboard/agent-management')
}

const openAgentDebug = () => {
  if (!installResult.value) return
  router.push({
    name: 'AgentDebug',
    query: {
      agent_id: installResult.value.agent.id,
      version_id: installResult.value.version.id,
      sample_question: installResult.value.sample_questions[0] || undefined,
    },
  })
}

watch(
  () => route.query.step,
  () => {
    currentStepIndex.value = stepFromQuery()
    normalizeCurrentStep()
  }
)

onMounted(async () => {
  currentStepIndex.value = stepFromQuery()
  await loadTemplate()
  await loadResourceOptions()
  await loadInstalledInstanceResult()
  normalizeCurrentStep()
  syncStepToRoute()
})
</script>

<template>
  <div class="space-y-4 sm:space-y-5">
    <button
      class="inline-flex items-center gap-2 text-sm font-medium text-gray-500 hover:text-gray-900"
      @click="router.push({ name: 'ScenarioTemplateDetail', params: route.params })"
    >
      <ArrowLeftIcon class="h-4 w-4" />
      返回模板详情
    </button>

    <div v-if="template" class="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
      <aside class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p class="text-xs font-semibold uppercase text-blue-600">交付向导</p>
        <h1 class="mt-2 text-lg font-bold text-gray-900">{{ template.name }}</h1>
        <div class="mt-5 space-y-2">
          <button
            v-for="(step, index) in steps"
            :key="step.id"
            class="flex w-full items-start gap-3 rounded-md p-2 text-left transition"
            :class="index === currentStepIndex ? 'bg-blue-50 text-blue-800' : 'hover:bg-gray-50'"
            @click="goStep(index)"
          >
            <span
              class="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold"
              :class="index <= currentStepIndex ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-500'"
            >
              {{ index + 1 }}
            </span>
            <span>
              <span class="block text-sm font-semibold">{{ step.title }}</span>
              <span class="block text-xs leading-5 text-gray-500">{{ step.hint }}</span>
            </span>
          </button>
        </div>
      </aside>

      <main class="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="border-b border-gray-200 p-5">
          <div class="text-xs font-semibold text-blue-600">第 {{ currentStepIndex + 1 }} 步 / {{ steps.length }}</div>
          <h2 class="mt-1 text-xl font-bold text-gray-900">{{ currentStep.title }}</h2>
          <p class="mt-1 text-sm text-gray-500">{{ currentStep.hint }}</p>
        </div>

        <div class="p-5">
          <section v-if="currentStep.id === 'info'" class="space-y-5">
            <div class="rounded-lg border border-gray-200 p-4">
              <h3 class="text-base font-semibold text-gray-900">填写信息</h3>
              <p class="mt-1 text-sm text-gray-500">先确认这次交付要创建的智能体实例。</p>
              <div class="mt-5 grid gap-4 md:grid-cols-2">
                <label class="block">
                  <span class="text-sm font-medium text-gray-700">实例标识</span>
                  <input v-model="form.instance_name" class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100">
                </label>
                <label class="block">
                  <span class="text-sm font-medium text-gray-700">显示名称</span>
                  <input v-model="form.display_name" class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100">
                </label>
                <label class="block">
                  <span class="text-sm font-medium text-gray-700">负责人</span>
                  <input v-model="form.owner" class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100" placeholder="交付负责人或业务负责人">
                </label>
                <label class="block md:col-span-2">
                  <span class="text-sm font-medium text-gray-700">说明</span>
                  <textarea v-model="form.description" rows="4" class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100" />
                </label>
              </div>
            </div>
          </section>

          <section v-else-if="currentStep.id === 'resources'" class="space-y-5">
            <div class="rounded-lg border border-amber-100 bg-amber-50 p-4">
              <h3 class="text-base font-semibold text-amber-900">绑定资源</h3>
              <p class="mt-2 text-sm leading-6 text-amber-800">必选资源会参与预检。没有绑定时不能安装，避免交付出一个看似成功但无法回答真实问题的半成品。</p>
            </div>
            <div class="grid gap-4 md:grid-cols-2">
              <div
                v-for="resource in template.required_resources"
                :key="`${resource.type}-${resource.name}`"
                class="rounded-lg border border-gray-200 p-4"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="text-sm font-semibold text-gray-900">{{ resource.name }}</span>
                  <span class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">{{ resource.required ? '必选' : '建议' }}</span>
                </div>
                <p class="mt-2 text-sm leading-6 text-gray-600">{{ resource.description }}</p>
                <div class="mt-4 space-y-2">
                  <label
                    v-for="option in optionsForResource(resource.type)"
                    :key="option.id"
                    class="flex cursor-pointer items-start gap-3 rounded-md border border-gray-200 p-3 hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      class="mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      :checked="(resourceBindings[resource.type] || []).includes(option.id)"
                      @change="toggleResourceBinding(resource.type, option.id)"
                    >
                    <span>
                      <span class="block text-sm font-medium text-gray-900">{{ option.label }}</span>
                      <span class="block text-xs text-gray-500">{{ resourceTypeLabel(resource.type) }} · {{ option.status || '可用' }}</span>
                      <span v-if="option.description" class="mt-1 block text-xs leading-5 text-gray-500">{{ option.description }}</span>
                    </span>
                  </label>
                  <div v-if="optionsForResource(resource.type).length === 0" class="rounded-md border border-dashed border-gray-300 p-3 text-sm text-gray-500">
                    暂无可绑定{{ resourceTypeLabel(resource.type) }}。这是{{ resource.required ? '必选' : '建议' }}资源，请先到对应资源管理页创建或发布后再继续。
                  </div>
                </div>
              </div>
            </div>
            <div v-if="requiredMissing.length > 0" class="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
              还缺少必选资源：{{ requiredMissing.map((item) => item.name).join('、') }}
            </div>
          </section>

          <section v-else-if="currentStep.id === 'install'" class="space-y-5">
            <div class="rounded-lg border border-gray-200 p-4">
              <h3 class="text-base font-semibold text-gray-900">预检安装</h3>
              <p class="mt-1 text-sm text-gray-500">先点预检确认名称、工具和资源状态，再执行一键安装。</p>
              <div class="mt-5 flex flex-wrap gap-3">
                <button
                  class="inline-flex items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="!canInstall || requiredMissing.length > 0 || prechecking"
                  @click="runPrecheck"
                >
                  <BeakerIcon class="h-4 w-4" />
                  {{ prechecking ? '预检中' : '预检' }}
                </button>
                <button
                  class="inline-flex items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="!canInstall || requiredMissing.length > 0 || installing"
                  @click="installTemplate"
                >
                  <RocketLaunchIcon class="h-4 w-4" />
                  {{ installing ? '安装中' : '一键安装' }}
                </button>
              </div>
            </div>

            <div v-if="precheckResult" class="space-y-2">
              <div
                v-for="check in precheckResult.checks"
                :key="check.key"
                class="flex items-start gap-3 rounded-md border px-3 py-2"
                :class="statusClass(check.status)"
              >
                <CheckCircleIcon v-if="check.status !== 'error'" class="mt-0.5 h-4 w-4 flex-shrink-0" />
                <XCircleIcon v-else class="mt-0.5 h-4 w-4 flex-shrink-0" />
                <div>
                  <div class="text-sm font-medium">{{ check.label }}</div>
                  <div class="text-xs leading-5">{{ check.message }}</div>
                </div>
              </div>
            </div>
          </section>

          <section v-else class="space-y-5">
            <div v-if="installResult" class="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
              <h3 class="text-base font-semibold text-emerald-900">交付清单</h3>
              <p class="mt-2 text-sm text-emerald-800">
                {{ installResult.created ? '已创建新智能体' : '已复用现有智能体' }}：
                {{ installResult.agent.display_name }}（{{ installResult.agent.name }}），版本状态 {{ installResult.version.status }}。
              </p>
              <div class="mt-4 grid gap-2 text-sm text-emerald-800 md:grid-cols-2">
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">已发布 v{{ installResult.version.version_number }}</div>
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">实例记录 {{ installResult.instance.id }}</div>
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">安装记录 {{ installResult.run.id }}</div>
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">已绑定 {{ Object.keys(installResult.resource_bindings || {}).length }} 类资源</div>
              </div>
              <div class="mt-4 grid gap-3 lg:grid-cols-2">
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">
                  <div class="text-xs font-semibold uppercase text-emerald-700">绑定明细</div>
                  <ul class="mt-2 space-y-1 text-sm text-emerald-800">
                    <li v-for="resource in installResult.resource_summary" :key="resource.type" class="flex items-start justify-between gap-3">
                      <span>{{ resource.label }}</span>
                      <span class="text-right text-xs text-emerald-700">{{ resource.count }} 个：{{ (resource.names?.length ? resource.names : resource.ids).join('、') }}</span>
                    </li>
                  </ul>
                </div>
                <div class="rounded-md border border-emerald-200 bg-white/70 p-3">
                  <div class="text-xs font-semibold uppercase text-emerald-700">已启用工具</div>
                  <div class="mt-2 flex flex-wrap gap-2">
                    <span v-for="tool in installResult.enabled_tools" :key="tool" class="rounded-full bg-emerald-100 px-2 py-1 text-xs font-medium text-emerald-800">{{ tool }}</span>
                  </div>
                </div>
              </div>
              <div class="mt-4 rounded-md border border-emerald-200 bg-white/70 p-3">
                <div class="text-xs font-semibold uppercase text-emerald-700">验收动作</div>
                <ul class="mt-2 space-y-1 text-sm text-emerald-800">
                  <li v-for="criterion in template.acceptance_criteria" :key="criterion">· {{ criterion }}</li>
                </ul>
              </div>
              <div class="mt-4 rounded-md border border-emerald-200 bg-white/70 p-3">
                <div class="text-xs font-semibold uppercase text-emerald-700">推荐验收问题</div>
                <div class="mt-2 grid gap-2 md:grid-cols-2">
                  <div v-for="question in installResult.sample_questions" :key="question" class="rounded border border-emerald-100 bg-white px-3 py-2 text-sm text-emerald-800">
                    {{ question }}
                  </div>
                </div>
              </div>
              <div class="mt-4">
                <div class="text-xs font-semibold uppercase text-emerald-700">next_steps</div>
                <ul class="mt-2 space-y-2 text-sm text-emerald-800">
                  <li v-for="step in installResult.next_steps" :key="step" class="flex gap-2">
                    <span class="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-emerald-500" />
                    <span>{{ step }}</span>
                  </li>
                </ul>
              </div>
              <div class="mt-4 flex flex-wrap gap-3">
                <button class="inline-flex items-center justify-center rounded-md bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800" @click="openAgentDebug">
                  打开调试台
                </button>
                <button class="inline-flex items-center justify-center rounded-md border border-emerald-300 bg-white px-4 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-50" @click="openAgentCenter">
                  去智能体中心
                </button>
              </div>
            </div>
            <div v-else class="rounded-lg border border-dashed border-gray-300 p-8 text-center">
              <h3 class="text-base font-semibold text-gray-900">还没有完成安装</h3>
              <p class="mt-2 text-sm text-gray-500">请先回到“预检安装”步骤执行一键安装，完成后这里会显示交付清单。</p>
            </div>
          </section>
        </div>

        <div class="flex items-center justify-between border-t border-gray-200 p-5">
          <button
            class="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="currentStepIndex === 0"
            @click="goPrev"
          >
            <ChevronLeftIcon class="h-4 w-4" />
            上一步
          </button>
          <button
            class="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="currentStepIndex === steps.length - 1 || !canGoNext"
            @click="goNext"
          >
            下一步
            <ChevronRightIcon class="h-4 w-4" />
          </button>
        </div>
      </main>
    </div>
  </div>
</template>
