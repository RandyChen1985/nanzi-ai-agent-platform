<script setup lang="ts">
/**
 * 业务分组页签条：API 工具页与脚本工具页共用。
 *
 * 为什么抽成组件而不是各写一份：页签顺序、未分组殿后、溢出出口、无障碍属性必须两页
 * 完全一致。复制两份的下场是「API 页能点『更多分组』，脚本页的页签被截在屏幕外」——
 * 用户第一反应会是脚本页坏了，而不是那边少写了一段。
 *
 * 组件只管渲染和把点击回传，分组状态仍归父组件的 `groupFilter`：筛选面板里的分组下拉
 * 与页签共用同一份状态，切哪边另一边同步，不存在两份真相。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

interface GroupTabItem {
  key: string
  label: string
  count: number
}

const props = defineProps<{
  items: GroupTabItem[]
  /** 当前分组；'all' 表示不过滤 */
  modelValue: string
  /** 无障碍组名（两页文案不同：API 工具 / 脚本工具） */
  ariaLabel?: string
}>()

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const scrollRef = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
/** 页签放不下才给「更多分组」入口；6 个组也挂个多余按钮反而碍事 */
const overflowing = ref(false)
let observer: ResizeObserver | null = null

const select = (key: string) => {
  emit('update:modelValue', key)
  menuOpen.value = false
}

const measure = () => {
  const el = scrollRef.value
  if (!el) return
  // 4px 容差：浏览器缩放后 scrollWidth 会多算一两个像素，不容差会永远判定溢出
  overflowing.value = el.scrollWidth - el.clientWidth > 4
  if (!overflowing.value) menuOpen.value = false
}

const onDocumentClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null
  if (target?.closest('[data-group-tab-more]')) return
  menuOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  window.addEventListener('resize', measure)
  // 只监听 resize 会漏掉「侧栏折叠」「分组增删」这两类宽度变化
  if (typeof ResizeObserver !== 'undefined' && scrollRef.value) {
    observer = new ResizeObserver(() => measure())
    observer.observe(scrollRef.value)
  }
  nextTick(measure)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  window.removeEventListener('resize', measure)
  observer?.disconnect()
  observer = null
})

watch(
  () => props.items,
  () => nextTick(measure),
)
</script>

<template>
  <!-- 只有一个「全部」时不渲染整条：还没有任何分组的部署上，挂一个孤零零的页签比没有更怪 -->
  <div
    v-if="items.length > 1"
    class="flex items-center gap-1 border-b border-gray-100 px-4"
    role="tablist"
    :aria-label="ariaLabel || '按业务分组筛选'"
  >
    <div
      ref="scrollRef"
      class="-mb-px flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        role="tab"
        :aria-selected="modelValue === item.key"
        class="relative flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-t-lg px-2.5 pt-2 pb-2.5 text-xs font-medium transition-colors"
        :class="modelValue === item.key ? 'text-primary' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'"
        @click="select(item.key)"
      >
        {{ item.label }}
        <span
          class="rounded px-1 text-[10px] font-semibold tabular-nums"
          :class="modelValue === item.key ? 'bg-primary/10 text-primary' : 'bg-gray-100 text-gray-500'"
        >{{ item.count }}</span>
        <span v-if="modelValue === item.key" class="absolute inset-x-1.5 -bottom-px h-0.5 rounded-t bg-primary"></span>
      </button>
    </div>

    <div v-if="overflowing" class="relative shrink-0 pb-1.5" data-group-tab-more>
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
        :aria-expanded="menuOpen"
        aria-haspopup="true"
        @click.stop="menuOpen = !menuOpen"
      >
        更多分组
        <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-180': menuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 9l6 6 6-6" />
        </svg>
      </button>
      <div
        v-if="menuOpen"
        class="absolute right-0 top-full z-30 mt-1 max-h-64 w-56 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-xl"
      >
        <button
          v-for="item in items"
          :key="`menu-${item.key}`"
          type="button"
          class="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-gray-50"
          :class="modelValue === item.key ? 'bg-primary/5 font-medium text-primary' : 'text-gray-700'"
          @click="select(item.key)"
        >
          <span class="truncate">{{ item.label }}</span>
          <span class="shrink-0 text-[10px] text-gray-400 tabular-nums">{{ item.count }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
