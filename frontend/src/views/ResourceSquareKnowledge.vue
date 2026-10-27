<script setup lang="ts">
/**
 * 知识库只读概览。
 *
 * 为什么单独一页而不是复用知识库管理页：进入管理页需要 `menu:*` 权限，
 * 只被授权访问某个库的普通用户会被路由守卫挡回去——批了权限却打不开。
 *
 * 边界：只给库信息与文档目录，不给切片正文和原文下载。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchKnowledgeReadonly, resourceSquareErrorStatus } from '@/api/resourceSquare'
import type { KnowledgeReadonly } from '@/types/resourceSquare'

const route = useRoute()
const router = useRouter()
const datasetId = String(route.params.datasetId || '')

const data = ref<KnowledgeReadonly | null>(null)
const loading = ref(true)
const error = ref('')
const forbidden = ref(false)

const visibilityLabel = computed(() =>
  data.value?.visibility === 'public' ? '公共级' : data.value?.visibility === 'team' ? '部门级' : '个人私有',
)

function backToSquare() {
  router.push({ path: '/dashboard/workbench', query: { tab: 'square', section: 'knowledge' } })
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
    data.value = await fetchKnowledgeReadonly(datasetId)
  } catch (err) {
    forbidden.value = resourceSquareErrorStatus(err) === 403
    error.value = err instanceof Error ? err.message : '读取知识库失败'
    // 白屏时最需要的就是这条：没有它只能靠猜
    console.error('[ResourceSquare] 读取知识库概览失败', { datasetId, err })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <!-- 骨架块用 bg-gray-100 时与页面背景几乎同色，加载中看起来就是一片空白：
         给白底 + 边框，并写明在读什么 -->
    <div v-if="loading" class="space-y-3">
      <div class="flex items-center gap-2.5 rounded-2xl border border-gray-100 bg-white px-4 py-5 dark:border-gray-700 dark:bg-gray-800">
        <span class="h-4 w-4 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
        <span class="text-sm text-gray-500 dark:text-gray-400">正在读取知识库…</span>
      </div>
      <div class="h-56 animate-pulse rounded-2xl border border-gray-100 bg-white dark:border-gray-700 dark:bg-gray-800" />
    </div>

    <div
      v-else-if="forbidden"
      class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center dark:border-gray-700"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">你还没有这个知识库的访问权限。</p>
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
      <div
        v-if="data.documents_error"
        class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
      >
        文档列表暂时读不到（知识库引擎未响应），库信息仍然有效，稍后可重试。
      </div>

      <div class="rounded-2xl border border-gray-100 bg-white p-5 dark:border-gray-700 dark:bg-gray-800">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h1 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
              <span class="truncate">{{ data.name }}</span>
              <span class="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                {{ visibilityLabel }}
              </span>
              <span class="rounded-md bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400">
                只读
              </span>
            </h1>
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
              归属 {{ data.owner || '—' }} ·
              {{ data.documents_error ? '文档数未取到' : `${data.documents.length} 个文档` }} · 更新于
              {{ formatTime(data.updated_at) }}
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
        你拥有这个知识库的只读权限，可以浏览文档目录并在对话中检索；上传、删除文档或调整可见范围请联系管理员。出于审计要求，这里不提供原文下载。
      </div>

      <div class="overflow-hidden rounded-2xl border border-gray-100 bg-white dark:border-gray-700 dark:bg-gray-800">
        <table class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5 font-medium">文档</th>
              <th class="px-4 py-2.5 font-medium">类型</th>
              <th class="px-4 py-2.5 font-medium">切片</th>
              <th class="px-4 py-2.5 font-medium">解析状态</th>
              <th class="px-4 py-2.5 font-medium">更新时间</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="doc in data.documents" :key="doc.id">
              <td class="max-w-[320px] truncate px-4 py-2.5 text-gray-700 dark:text-gray-200">{{ doc.name }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ doc.file_type || '—' }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ doc.chunk_count ?? '—' }}</td>
              <td class="px-4 py-2.5">
                <span class="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-400">
                  {{ doc.run_status || '已解析' }}
                </span>
              </td>
              <td class="px-4 py-2.5 text-gray-400">{{ formatTime(doc.updated_at) }}</td>
            </tr>
            <tr v-if="!data.documents.length">
              <td colspan="5" class="px-4 py-8 text-center text-gray-400">
                {{ data.documents_error ? '文档列表暂时读不到，请稍后重试。' : '这个知识库还没有文档。' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 兜底：接口返回空但不报错时也要有话说，不能留一片空白 -->
    <div
      v-else
      class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center dark:border-gray-700"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">没有拿到这个知识库的信息。</p>
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
