<script setup lang="ts">
/**
 * 资源广场卡片：知识库与数据集共用一张卡，四种状态互斥。
 *
 * 状态优先级 has > pending > rejected > apply：
 * 有权限就不该再看到「申请」，正在审批中也不该看到「已驳回」——
 * 一个资源同时出现两个按钮，用户就不知道该点哪个。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

import { copyToClipboard } from '@/utils/clipboard'

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

/**
 * 复制资源名。
 *
 * 复制的是物理名（/ 知识库名）而不是副标题：卡片主标题是 `meta_pgdemo_ds` 这种
 * 可以直接拿去查库、写 SQL、配数据源的名字，中文副标题复制出去没用。
 */
const copied = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined

async function copyName() {
  if (!props.name) return
  const ok = await copyToClipboard(props.name)
  if (!ok) return
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copied.value = false
  }, 1500)
}

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})

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
    class="group flex flex-col rounded-2xl border bg-white p-4 transition-all dark:bg-gray-800/80"
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
        <!-- flex 而非 truncate：h4 一旦 overflow:hidden，标题一长就把复制浮标裁掉，
             而按钮正是长标题时最需要露出来的。让名字与副标题各自截断，浮标固定不缩。-->
        <h4
          class="flex min-w-0 items-baseline gap-1 text-[13.5px] font-semibold text-gray-900 dark:text-gray-100"
        >
          <button
            type="button"
            class="min-w-0 truncate text-left"
            :class="
              state === 'has' && kind !== 'kb'
                ? 'cursor-pointer hover:text-primary'
                : 'cursor-default'
            "
            @click="onTitleClick"
          >
            {{ name }}
          </button>
          <span v-if="subtitle" class="min-w-0 truncate text-xs font-normal text-gray-400">{{
            subtitle
          }}</span>
          <!-- 复制浮标：鼠标移入卡片才出现，避免每张卡都挂一个图标把标题挤掉。
               触屏没有 hover，小屏常显。
               复制成功只把图标换成勾、不加「已复制」三个字——加了会把标题挤短一截，
               而反馈靠颜色与图标已经足够，完整提示在 title 里。-->
          <button
            type="button"
            class="ml-auto inline-flex shrink-0 items-center gap-0.5 rounded px-1 py-0.5 text-[10px] font-normal text-gray-400 opacity-0 transition-opacity hover:bg-gray-100 hover:text-gray-600 focus-visible:opacity-100 group-hover:opacity-100 max-sm:opacity-100 dark:hover:bg-gray-700"
            :title="copied ? '已复制' : `复制 ${name}`"
            :aria-label="copied ? '已复制' : `复制 ${name}`"
            @click.stop="copyName"
          >
            <svg
              v-if="!copied"
              class="h-3 w-3"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <rect x="9" y="9" width="12" height="12" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
            <svg
              v-else
              class="h-3 w-3 text-emerald-600"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.4"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </button>
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
