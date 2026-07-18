<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeftIcon,
  ClipboardDocumentCheckIcon,
  CubeTransparentIcon,
  RocketLaunchIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/vue/24/outline'
import { portalApi } from '../api/portal'
import type { ScenarioTemplateDetail } from '../api/portal'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'

const route = useRoute()
const router = useRouter()
const { showToast } = useToast()
const { hasPermission } = useUser()
const canInstall = computed(
  () => hasPermission('menu:agent_management') || hasPermission('element:agent:create')
)

const template = ref<ScenarioTemplateDetail | null>(null)
const loading = ref(false)

const loadTemplate = async () => {
  const templateId = String(route.params.templateId || '')
  if (!templateId) return
  loading.value = true
  try {
    const res = await portalApi.getScenarioTemplate(templateId)
    template.value = res.data.data || null
  } catch (error) {
    showToast('获取模板详情失败', 'error')
  } finally {
    loading.value = false
  }
}

const startInstall = () => {
  if (!template.value) return
  if (!canInstall.value) {
    showToast('当前账号无交付权限，请联系管理员开通智能体管理权限', 'warning')
    return
  }
  router.push({ name: 'ScenarioTemplateInstall', params: { templateId: template.value.id }, query: { step: 'info' } })
}

onMounted(loadTemplate)
</script>

<template>
  <div class="space-y-4 sm:space-y-5">
    <button
      class="inline-flex items-center gap-2 text-sm font-medium text-gray-500 hover:text-gray-900"
      @click="router.push({ name: 'ScenarioTemplates' })"
    >
      <ArrowLeftIcon class="h-4 w-4" />
      返回场景包市场
    </button>

    <div v-if="template" class="space-y-5">
      <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p class="text-xs font-semibold uppercase text-blue-600">模板详情</p>
            <h1 class="mt-2 text-2xl font-bold text-gray-900">{{ template.name }}</h1>
            <p class="mt-2 max-w-3xl text-sm leading-6 text-gray-600">{{ template.description }}</p>
            <div class="mt-4 flex flex-wrap gap-2">
              <span class="rounded bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">{{ template.category }}</span>
              <span class="rounded bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">{{ template.maturity || '基础版' }}</span>
              <span class="rounded bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">预计交付 {{ template.delivery_time || '待评估' }}</span>
            </div>
          </div>
          <button
            v-if="canInstall"
            class="inline-flex items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            @click="startInstall"
          >
            <RocketLaunchIcon class="h-4 w-4" />
            开始交付
          </button>
          <p v-else class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            可浏览方案说明；交付安装需管理员开通智能体管理权限。
          </p>
        </div>
      </section>

      <section class="grid gap-5 xl:grid-cols-3">
        <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 class="flex items-center gap-2 text-base font-semibold text-gray-900">
            <CubeTransparentIcon class="h-5 w-5 text-blue-500" />
            业务目标
          </h2>
          <ul class="mt-4 space-y-3 text-sm leading-6 text-gray-600">
            <li v-for="goal in template.business_goals" :key="goal" class="flex gap-2">
              <span class="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-blue-500" />
              <span>{{ goal }}</span>
            </li>
          </ul>
        </div>

        <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 class="flex items-center gap-2 text-base font-semibold text-gray-900">
            <WrenchScrewdriverIcon class="h-5 w-5 text-slate-500" />
            交付物
          </h2>
          <div class="mt-4 flex flex-wrap gap-2">
            <span
              v-for="item in template.deliverables"
              :key="item"
              class="rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-700"
            >
              {{ item }}
            </span>
          </div>
        </div>

        <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 class="flex items-center gap-2 text-base font-semibold text-gray-900">
            <ClipboardDocumentCheckIcon class="h-5 w-5 text-emerald-500" />
            验收标准
          </h2>
          <ul class="mt-4 space-y-3 text-sm leading-6 text-gray-600">
            <li v-for="criteria in template.acceptance_criteria" :key="criteria" class="flex gap-2">
              <span class="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-emerald-500" />
              <span>{{ criteria }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section class="grid gap-5 lg:grid-cols-2">
        <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 class="text-base font-semibold text-gray-900">适用部门</h2>
          <div class="mt-4 flex flex-wrap gap-2">
            <span
              v-for="dept in template.target_departments"
              :key="dept"
              class="rounded border border-gray-200 px-2.5 py-1 text-sm text-gray-600"
            >
              {{ dept }}
            </span>
          </div>
        </div>

        <div class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 class="text-base font-semibold text-gray-900">验收样例</h2>
          <div class="mt-4 space-y-2">
            <div
              v-for="question in template.sample_questions"
              :key="question"
              class="rounded-md border border-gray-100 px-3 py-2 text-sm text-gray-700"
            >
              {{ question }}
            </div>
          </div>
        </div>
      </section>
    </div>

    <div v-else-if="!loading" class="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
      未找到该场景模板
    </div>
  </div>
</template>
