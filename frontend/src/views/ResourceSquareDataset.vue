<script setup lang="ts">
/**
 * 数据集只读概览。
 *
 * 为什么单独一页：进入元数据管理页需要 `menu:metadata`，只被授权某个数据集的
 * 普通用户打不开它——授权记录写了，人却看不到数据是什么。
 *
 * 边界：只给表清单、指标口径、质量分；不给字段明细、样例值、YAML、
 * 行级过滤配置与数据源连接信息。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchDatasetReadonly, resourceSquareErrorStatus } from '@/api/resourceSquare'
import type { DatasetReadonly } from '@/types/resourceSquare'

const route = useRoute()
const router = useRouter()
const datasetId = Number(route.params.id)

const data = ref<DatasetReadonly | null>(null)
const loading = ref(true)
const error = ref('')
const forbidden = ref(false)
const activeSection = ref<'tables' | 'metrics'>('tables')

const kpis = computed(() => {
  if (!data.value) return []
  return [
    { label: '物理表', value: String(data.value.tables.length), tone: 'text-gray-900 dark:text-gray-100' },
    { label: '指标', value: String(data.value.metrics.length), tone: 'text-gray-900 dark:text-gray-100' },
    { label: '表关系', value: String(data.value.relationship_count), tone: 'text-gray-900 dark:text-gray-100' },
    {
      label: '质量分',
      value: data.value.quality_score === null ? '—' : String(data.value.quality_score),
      tone: 'text-emerald-600',
    },
  ]
})

function backToSquare() {
  router.push({ path: '/dashboard/workbench', query: { tab: 'square', section: 'datasets' } })
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
}

async function load() {
  loading.value = true
  forbidden.value = false
  error.value = ''
  try {
    data.value = await fetchDatasetReadonly(datasetId)
  } catch (err) {
    forbidden.value = resourceSquareErrorStatus(err) === 403
    error.value = err instanceof Error ? err.message : '读取数据集失败'
    console.error('[ResourceSquare] 读取数据集概览失败', { datasetId, err })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div v-if="loading" class="space-y-3">
      <div class="flex items-center gap-2.5 rounded-2xl border border-gray-100 bg-white px-4 py-5 dark:border-gray-700 dark:bg-gray-800">
        <span class="h-4 w-4 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
        <span class="text-sm text-gray-500 dark:text-gray-400">正在读取数据集…</span>
      </div>
      <div class="h-56 animate-pulse rounded-2xl border border-gray-100 bg-white dark:border-gray-700 dark:bg-gray-800" />
    </div>

    <div
      v-else-if="forbidden"
      class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center dark:border-gray-700"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">你还没有这个数据集的访问权限。</p>
      <button
        type="button"
        class="mt-3 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-dark"
        @click="backToSquare"
      >
        去资源广场申请
      </button>
    </div>

    <div
      v-else-if="error"
      class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
    >
      {{ error }}
      <button type="button" class="ml-2 font-semibold underline" @click="load">重试</button>
    </div>

    <template v-else-if="data">
      <div class="rounded-2xl border border-gray-100 bg-white p-5 dark:border-gray-700 dark:bg-gray-800">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h1 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
              <span class="truncate font-mono">{{ data.name }}</span>
              <span class="text-sm font-normal text-gray-400">{{ data.display_name }}</span>
              <span class="rounded-md bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400">
                只读
              </span>
            </h1>
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
              数据源 {{ data.data_source || '—' }} · 更新于 {{ formatTime(data.updated_at) }}
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:border-primary hover:text-primary dark:border-gray-600 dark:text-gray-300"
            @click="backToSquare"
          >
            返回广场
          </button>
        </div>
        <p v-if="data.description" class="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
          {{ data.description }}
        </p>
      </div>

      <div
        class="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs leading-6 text-gray-600 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-gray-300"
      >
        你拥有这个数据集的只读权限，可以浏览表结构与指标口径，并直接在对话中查询；编辑元数据、调整行级权限请联系管理员。
      </div>

      <div class="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <div
          v-for="kpi in kpis"
          :key="kpi.label"
          class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"
        >
          <p class="text-[11px] text-gray-500 dark:text-gray-400">{{ kpi.label }}</p>
          <p class="mt-0.5 text-xl font-bold" :class="kpi.tone">{{ kpi.value }}</p>
        </div>
      </div>

      <div class="overflow-hidden rounded-2xl border border-gray-100 bg-white dark:border-gray-700 dark:bg-gray-800">
        <div class="flex gap-5 border-b border-gray-100 px-4 dark:border-gray-700/60">
          <button
            type="button"
            class="-mb-px border-b-2 py-2.5 text-xs font-semibold transition-colors"
            :class="activeSection === 'tables' ? 'border-primary text-primary' : 'border-transparent text-gray-500'"
            @click="activeSection = 'tables'"
          >
            表结构
          </button>
          <button
            type="button"
            class="-mb-px border-b-2 py-2.5 text-xs font-semibold transition-colors"
            :class="activeSection === 'metrics' ? 'border-primary text-primary' : 'border-transparent text-gray-500'"
            @click="activeSection = 'metrics'"
          >
            指标
          </button>
        </div>

        <table v-if="activeSection === 'tables'" class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5 font-medium">物理表</th>
              <th class="px-4 py-2.5 font-medium">业务术语</th>
              <th class="px-4 py-2.5 font-medium">字段数</th>
              <th class="px-4 py-2.5 font-medium">说明</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="table in data.tables" :key="table.physical_name">
              <td class="px-4 py-2.5 font-mono text-gray-700 dark:text-gray-200">{{ table.physical_name }}</td>
              <td class="px-4 py-2.5 text-gray-600 dark:text-gray-300">{{ table.term }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ table.field_count }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ table.description || '—' }}</td>
            </tr>
            <tr v-if="!data.tables.length">
              <td colspan="4" class="px-4 py-8 text-center text-gray-400">这个数据集还没有登记表结构。</td>
            </tr>
          </tbody>
        </table>

        <table v-else class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5 font-medium">指标</th>
              <th class="px-4 py-2.5 font-medium">显示名</th>
              <th class="px-4 py-2.5 font-medium">口径</th>
              <th class="px-4 py-2.5 font-medium">单位</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="metric in data.metrics" :key="metric.name">
              <td class="px-4 py-2.5 font-mono text-gray-700 dark:text-gray-200">{{ metric.name }}</td>
              <td class="px-4 py-2.5 text-gray-600 dark:text-gray-300">{{ metric.display_name || '—' }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ metric.description || '—' }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ metric.unit || '—' }}</td>
            </tr>
            <tr v-if="!data.metrics.length">
              <td colspan="4" class="px-4 py-8 text-center text-gray-400">这个数据集还没有登记指标。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div
      v-else
      class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center dark:border-gray-700"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">没有拿到这个数据集的信息。</p>
      <button
        type="button"
        class="mt-3 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:border-primary hover:text-primary dark:border-gray-600 dark:text-gray-300"
        @click="load"
      >
        重新加载
      </button>
    </div>
  </div>
</template>
