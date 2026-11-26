<script setup lang="ts">
import { computed } from 'vue'
import type { AIModel, AIModelReference } from '../../api/model'

const props = withDefaults(defineProps<{
  open: boolean
  model: AIModel | null
  references: AIModelReference[]
  loading?: boolean
  error?: string
}>(), {
  loading: false,
  error: '',
})

const emit = defineEmits<{
  close: []
}>()

const visibleReferences = computed(() => props.references.filter((reference) =>
  reference.kind !== 'agent_version'
  || reference.version_status === 'PUBLISHED'
  || reference.version_status === 'DRAFT'
))
const systemReferences = computed(() => visibleReferences.value.filter((reference) => reference.kind === 'system_config'))
const agentReferences = computed(() => visibleReferences.value.filter((reference) => reference.kind === 'agent_version'))

const statusLabels: Record<string, string> = {
  DRAFT: '草稿',
  PUBLISHED: '已发布',
  ARCHIVED: '已归档',
}

const statusClass = (status?: string) => {
  if (status === 'PUBLISHED') return 'bg-green-50 text-green-700'
  if (status === 'ARCHIVED') return 'bg-gray-100 text-gray-500'
  return 'bg-amber-50 text-amber-700'
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="model-usage-title"
    @click.self="emit('close')"
  >
    <div class="absolute inset-0 bg-slate-950/35 backdrop-blur-[1px]" @click="emit('close')"></div>
    <aside class="absolute right-0 top-0 flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
      <header class="shrink-0 border-b border-slate-200 px-5 py-4">
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0">
            <p class="text-xs font-medium uppercase tracking-wide text-primary">模型使用关系中心</p>
            <h2 id="model-usage-title" class="mt-1 truncate text-lg font-semibold text-slate-900">
              {{ model?.name || '模型' }}
            </h2>
            <p class="mt-1 truncate font-mono text-xs text-slate-500" :title="model?.model_id">
              {{ model?.model_id }}
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="关闭模型使用关系"
            title="关闭"
            @click="emit('close')"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="mt-4 grid grid-cols-3 gap-2">
          <div class="rounded-lg bg-slate-50 px-3 py-2">
            <p class="text-xs text-slate-500">全部关系</p>
            <p class="mt-1 text-lg font-semibold text-slate-900">{{ visibleReferences.length }}</p>
          </div>
          <div class="rounded-lg bg-primary/5 px-3 py-2">
            <p class="text-xs text-primary">智能体版本</p>
            <p class="mt-1 text-lg font-semibold text-primary-dark">{{ agentReferences.length }}</p>
          </div>
          <div class="rounded-lg bg-amber-50 px-3 py-2">
            <p class="text-xs text-amber-700">系统配置</p>
            <p class="mt-1 text-lg font-semibold text-amber-800">{{ systemReferences.length }}</p>
          </div>
        </div>
      </header>

      <main class="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        <div v-if="loading" class="flex min-h-48 items-center justify-center text-sm text-slate-500">
          正在读取使用关系…
        </div>
        <div v-else-if="error" class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {{ error }}
        </div>
        <div v-else-if="visibleReferences.length === 0" class="flex min-h-48 flex-col items-center justify-center text-center">
          <div class="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
            <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p class="mt-3 text-sm font-medium text-slate-700">当前没有发现配置引用</p>
          <p class="mt-1 text-xs text-slate-500">该模型暂未被系统默认配置或智能体版本使用。</p>
        </div>
        <div v-else class="space-y-6">
          <section v-if="systemReferences.length">
            <div class="mb-2 flex items-center justify-between">
              <h3 class="text-sm font-semibold text-slate-900">系统配置</h3>
              <span class="text-xs text-slate-400">{{ systemReferences.length }} 项</span>
            </div>
            <div class="space-y-2">
              <div
                v-for="reference in systemReferences"
                :key="`${reference.kind}-${reference.key}`"
                class="rounded-lg border border-slate-200 bg-white px-3 py-3"
              >
                <p class="text-sm font-medium text-slate-800">{{ reference.label }}</p>
                <p class="mt-1 break-all font-mono text-xs text-slate-500">{{ reference.detail }}</p>
              </div>
            </div>
          </section>

          <section v-if="agentReferences.length">
            <div class="mb-2 flex items-center justify-between">
              <div>
                <h3 class="text-sm font-semibold text-slate-900">智能体版本</h3>
                <p class="mt-0.5 text-xs text-slate-400">显示已发布和草稿版本，已归档版本不计入</p>
              </div>
              <span class="text-xs text-slate-400">{{ agentReferences.length }} 项</span>
            </div>
            <div class="space-y-2">
              <div
                v-for="reference in agentReferences"
                :key="`${reference.version_id || reference.label}-${reference.detail}`"
                class="rounded-lg border border-slate-200 bg-white px-3 py-3"
              >
                <div class="flex items-start justify-between gap-3">
                  <p class="min-w-0 truncate text-sm font-medium text-slate-800" :title="reference.label">
                    {{ reference.label }}
                  </p>
                  <span
                    v-if="reference.version_status"
                    class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium"
                    :class="statusClass(reference.version_status)"
                  >
                    {{ statusLabels[reference.version_status] || reference.version_status }}
                  </span>
                </div>
                <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span class="rounded-full bg-primary/5 px-2 py-0.5 text-primary-dark">{{ reference.detail }}</span>
                  <span v-if="reference.agent_enabled === false" class="rounded-full bg-red-50 px-2 py-0.5 text-red-700">智能体已停用</span>
                  <span v-if="reference.version_id" class="font-mono text-slate-400">{{ reference.version_id }}</span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>

      <footer class="shrink-0 border-t border-slate-200 bg-slate-50 px-5 py-3 text-xs leading-5 text-slate-500">
        禁用或删除模型后，上述配置仍会保留引用，但运行时将无法调用该模型。请先切换相关配置。
      </footer>
    </aside>
  </div>
</template>
