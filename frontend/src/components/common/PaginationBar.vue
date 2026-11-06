<script setup lang="ts">
/**
 * 统一分页条：知识库列表、库内文档列表、聊天资源抽屉共用一份。
 *
 * 为什么抽组件：Users.vue 与 AuditLogs.vue 各写过一套「上一页/下一页」，
 * 样式与越界行为都不一样；知识库这两个列表又要再加两套。这里把
 * 「总数 / 区间 / 页码窗口 / 每页条数 / 跳页」一次做完，页面只负责发请求。
 *
 * 约定：
 * - 受控组件，不持有页码状态，翻页只 emit；
 * - 页码算法（≤7 全显示、否则 1 … 当前±1 … 末页）在 `utils/paginationWindow.ts`；
 * - 跳页输入复用 `parsePageInput`（空值/乱输/越界都不炸）。
 */
import { computed, ref, watch } from 'vue'
import { parsePageInput } from '@/utils/pagination'
import {
  buildPageWindow,
  clampPage,
  pageRangeOf,
  totalPagesOf,
} from '@/utils/paginationWindow'

const props = withDefaults(
  defineProps<{
    /** 过滤后的真实总数（不是当前页条数） */
    total: number
    page: number
    pageSize: number
    /** 可选的每页条数；传空数组则隐藏选择器 */
    pageSizes?: number[]
    /** 计数单位，用于「共 N 个知识库」这类文案 */
    unit?: string
    /** 关掉跳页输入（窄容器里用，比如抽屉） */
    hideJump?: boolean
    /** 关掉每页条数选择（抽屉用「加载更多」时不需要） */
    hideSize?: boolean
    /** 右侧额外的说明位（比如「跨页勾选已保留」） */
    note?: string
  }>(),
  {
    pageSizes: () => [20, 50, 100],
    unit: '项',
    hideJump: false,
    hideSize: false,
    note: '',
  },
)

const emit = defineEmits<{
  (e: 'update:page', page: number): void
  (e: 'update:pageSize', size: number): void
}>()

const totalPages = computed(() => totalPagesOf(props.total, props.pageSize))
const currentPage = computed(() => clampPage(props.page, totalPages.value))
const windowPages = computed(() => buildPageWindow(currentPage.value, totalPages.value))
const range = computed(() => pageRangeOf(props.total, currentPage.value, props.pageSize))

const jumpInput = ref('')

// 翻页后清空跳页框，避免上一页的输入残留（比如输了 999 被夹到末页，下次还显示 999）
watch(currentPage, () => {
  jumpInput.value = ''
})

function go(target: number) {
  const next = clampPage(target, totalPages.value)
  if (next !== currentPage.value) emit('update:page', next)
}

function commitJump() {
  const next = parsePageInput(jumpInput.value, currentPage.value, totalPages.value)
  jumpInput.value = ''
  go(next)
}

function onJumpKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    event.preventDefault()
    commitJump()
  }
}

function onSizeChange(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value)
  if (!Number.isFinite(value) || value <= 0 || value === props.pageSize) return
  emit('update:pageSize', value)
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 border-t border-gray-200 px-3 py-2 dark:border-gray-700">
    <span class="whitespace-nowrap text-[11px] text-gray-500 dark:text-gray-400">
      共 <b class="font-semibold tabular-nums text-gray-700 dark:text-gray-200">{{ total.toLocaleString() }}</b> {{ unit }}
      <template v-if="total">
        · 当前 <b class="tabular-nums text-gray-700 dark:text-gray-200">{{ range.from }}-{{ range.to }}</b>
      </template>
      · 第 <b class="tabular-nums text-gray-700 dark:text-gray-200">{{ currentPage }}</b>/{{ totalPages }} 页
    </span>

    <span v-if="note" class="whitespace-nowrap text-[11px] text-gray-400 dark:text-gray-500">{{ note }}</span>

    <span class="flex-1" />

    <div class="flex items-center gap-1">
      <button
        type="button"
        class="flex h-7 min-w-[28px] items-center justify-center rounded-md border border-gray-200 bg-white px-1 text-gray-600 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-gray-200 disabled:hover:text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
        :disabled="currentPage <= 1"
        aria-label="上一页"
        @click="go(currentPage - 1)"
      >
        <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 5l-7 7 7 7" />
        </svg>
      </button>

      <template v-for="(item, index) in windowPages" :key="`${item}-${index}`">
        <span
          v-if="item === '…'"
          class="px-0.5 text-[11px] text-gray-400 dark:text-gray-500"
          aria-hidden="true"
        >…</span>
        <button
          v-else
          type="button"
          class="h-7 min-w-[28px] rounded-md border px-1.5 text-[11.5px] tabular-nums transition-colors"
          :class="item === currentPage
            ? 'border-primary bg-primary font-semibold text-white'
            : 'border-gray-200 bg-white text-gray-600 hover:border-primary hover:text-primary dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300'"
          :aria-current="item === currentPage ? 'page' : undefined"
          @click="go(item)"
        >{{ item }}</button>
      </template>

      <button
        type="button"
        class="flex h-7 min-w-[28px] items-center justify-center rounded-md border border-gray-200 bg-white px-1 text-gray-600 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-gray-200 disabled:hover:text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
        :disabled="currentPage >= totalPages"
        aria-label="下一页"
        @click="go(currentPage + 1)"
      >
        <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      </button>
    </div>

    <!-- 每页条数与跳页是两组独立控件，中间用细线分开，不然「50 条 跳至」会连成一串 -->
    <span
      v-if="!hideSize || !hideJump"
      class="mx-0.5 h-3.5 w-px shrink-0 bg-gray-200 dark:bg-gray-700"
      aria-hidden="true"
    />

    <label
      v-if="!hideSize"
      class="flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-500 dark:text-gray-400"
    >
      每页
      <!--
        原生 select 的箭头会占掉右侧固定宽度，配上 px-1 的窄内边距时三位数（100）
        会被裁成「5O」。改成 appearance-none + 自绘箭头，宽度按内容留足。
      -->
      <span class="relative inline-flex items-center">
        <select
          class="h-7 min-w-[64px] cursor-pointer appearance-none rounded-md border border-gray-200 bg-white pl-2 pr-6 text-[11px] tabular-nums text-gray-600 outline-none transition-colors hover:border-primary/60 focus:border-primary dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
          :value="pageSize"
          aria-label="每页条数"
          @change="onSizeChange"
        >
          <option v-for="size in pageSizes" :key="size" :value="size">{{ size }}</option>
        </select>
        <svg
          class="pointer-events-none absolute right-1.5 h-2.5 w-2.5 text-gray-400"
          fill="none"
          stroke="currentColor"
          stroke-width="2.4"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" />
        </svg>
      </span>
      条
    </label>

    <span
      v-if="!hideSize && !hideJump"
      class="mx-0.5 h-3.5 w-px shrink-0 bg-gray-200 dark:bg-gray-700"
      aria-hidden="true"
    />

    <label
      v-if="!hideJump"
      class="flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-500 dark:text-gray-400"
    >
      跳至
      <input
        v-model="jumpInput"
        type="text"
        inputmode="numeric"
        class="h-7 w-12 rounded-md border border-gray-200 bg-white text-center text-[11px] tabular-nums text-gray-700 outline-none transition-colors hover:border-primary/60 focus:border-primary dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
        aria-label="跳转到第几页"
        @keydown="onJumpKeydown"
        @blur="commitJump"
      />
      页
    </label>
  </div>
</template>
