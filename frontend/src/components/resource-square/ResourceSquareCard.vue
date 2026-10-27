<script setup lang="ts">
/**
 * 资源广场卡片：知识库与数据集共用一张卡，四种状态互斥。
 *
 * 状态优先级 has > pending > rejected > apply：
 * 有权限就不该再看到「申请」，正在审批中也不该看到「已驳回」——
 * 一个资源同时出现两个按钮，用户就不知道该点哪个。
 */
import { computed } from 'vue'

const props = defineProps<{
  kind: 'kb' | 'ds'
  name: string
  subtitle?: string | null
  description?: string | null
  visibility?: 'public' | 'team' | null
  owner?: string | null
  badges?: string[]
  metrics?: Array<{ label: string; value: string }>
  hasAccess: boolean
  requestStatus?: 'pending' | 'approved' | 'rejected' | 'cancelled' | null
}>()

const emit = defineEmits<{
  (event: 'apply'): void
  (event: 'open'): void
}>()

type CardState = 'apply' | 'has' | 'pending' | 'rejected'

const state = computed<CardState>(() => {
  if (props.hasAccess) return 'has'
  if (props.requestStatus === 'pending') return 'pending'
  if (props.requestStatus === 'rejected') return 'rejected'
  return 'apply'
})

const visibilityLabel = computed(() =>
  props.visibility === 'public' ? '公共级' : props.visibility === 'team' ? '部门级' : '',
)

/** 可点进只读概览的条件：已授权 + 非知识库。
 *
 * 知识库这一侧暂时不提供只读概览入口（点开是空白，尚未解决），
 * 与其给一个点了就白屏的入口，不如不给。数据集侧照常。 */
function onTitleClick() {
  if (state.value === 'has' && props.kind !== 'kb') emit('open')
}
</script>

<template>
  <article
    class="flex flex-col rounded-2xl border bg-white p-4 transition-all dark:bg-gray-800/80"
    :class="
      state === 'has'
        ? 'border-emerald-200 dark:border-emerald-700/50'
        : 'border-gray-100 hover:border-primary/40 hover:shadow-md dark:border-gray-700/70'
    "
  >
    <div class="flex items-start gap-3">
      <span
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
        :class="
          kind === 'kb'
            ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'
            : 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400'
        "
      >
        <svg
          v-if="kind === 'kb'"
          class="h-[18px] w-[18px]"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M12 6.3v13m0-13C10.8 5.5 9.2 5 7.5 5S4.2 5.5 3 6.3v13C4.2 18.5 5.8 18 7.5 18s3.3.5 4.5 1.3m0-13C13.2 5.5 14.8 5 16.5 5s3.3.5 4.5 1.3v13c-1.2-.8-2.8-1.3-4.5-1.3s-3.3.5-4.5 1.3"
          />
        </svg>
        <svg
          v-else
          class="h-[18px] w-[18px]"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
        >
          <ellipse cx="12" cy="6" rx="8" ry="3" />
          <path
            stroke-linecap="round"
            d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"
          />
        </svg>
      </span>

      <div class="min-w-0 flex-1">
        <h4 class="truncate text-[13.5px] font-semibold text-gray-900 dark:text-gray-100">
          <button
            type="button"
            class="text-left"
            :class="
              state === 'has' && kind !== 'kb'
                ? 'cursor-pointer hover:text-primary'
                : 'cursor-default'
            "
            @click="onTitleClick"
          >
            {{ name }}
          </button>
          <span v-if="subtitle" class="ml-1 text-xs font-normal text-gray-400">{{ subtitle }}</span>
        </h4>
        <div class="mt-1.5 flex flex-wrap gap-1.5">
          <span
            v-if="visibilityLabel"
            class="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"
          >
            {{ visibilityLabel }}
          </span>
          <span
            v-for="badge in badges || []"
            :key="badge"
            class="rounded-md bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 dark:bg-gray-700/60 dark:text-gray-400"
          >
            {{ badge }}
          </span>
        </div>
      </div>
    </div>

    <p class="mt-2.5 line-clamp-2 min-h-[37px] text-xs leading-5 text-gray-500 dark:text-gray-400">
      {{ description || '暂无描述' }}
    </p>

    <div
      class="mt-2.5 flex flex-wrap gap-3 border-t border-dashed border-gray-100 pt-2.5 text-[11px] text-gray-400 dark:border-gray-700/60"
    >
      <span v-for="metric in metrics || []" :key="metric.label">{{ metric.value }}</span>
    </div>

    <div class="mt-2.5 flex items-center justify-between gap-2">
      <span class="truncate text-[11px] text-gray-400">{{ owner || '' }}</span>

      <span
        v-if="state === 'has'"
        class="shrink-0 rounded-lg bg-emerald-50 px-2 py-1 text-[11.5px] font-semibold text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-400"
      >
        已有权限
      </span>
      <span
        v-else-if="state === 'pending'"
        class="shrink-0 rounded-lg bg-amber-50 px-2 py-1 text-[11.5px] font-semibold text-amber-600 dark:bg-amber-900/20 dark:text-amber-400"
      >
        审批中
      </span>
      <span v-else-if="state === 'rejected'" class="flex shrink-0 items-center gap-1.5">
        <span class="rounded-lg bg-red-50 px-2 py-1 text-[11.5px] font-semibold text-red-500 dark:bg-red-900/20">
          已驳回
        </span>
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-2 py-1 text-[11.5px] font-semibold text-gray-600 hover:border-primary hover:text-primary dark:border-gray-600 dark:text-gray-300"
          @click="emit('apply')"
        >
          重新申请
        </button>
      </span>
      <button
        v-if="state === 'apply'"
        type="button"
        class="shrink-0 rounded-lg bg-primary px-2.5 py-1.5 text-[11.5px] font-semibold text-white transition-colors hover:bg-primary-dark"
        @click="emit('apply')"
      >
        申请访问
      </button>
    </div>
  </article>
</template>
