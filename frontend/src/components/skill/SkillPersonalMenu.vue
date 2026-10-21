<script setup lang="ts">
import { computed } from 'vue'

/**
 * 「我的技能」内的二级菜单：我的技能 / 收到的分享 / 我的分享。
 *
 * 照搬「我的数据门户」的左栏结构（200px、分组标题、选中项蓝底白字）；
 * 窄屏渲染为横向胶囊 Tab，避免在个人中心里再叠一层底部导航。
 */

type PersonalMenuKey = 'mine' | 'received' | 'sent'

const props = withDefaults(defineProps<{
  active: PersonalMenuKey
  receivedCount?: number
  sentPendingCount?: number
  variant?: 'aside' | 'chips'
}>(), {
  receivedCount: 0,
  sentPendingCount: 0,
  variant: 'aside',
})

const emit = defineEmits<{
  (e: 'select', key: PersonalMenuKey): void
}>()

interface MenuItem {
  key: PersonalMenuKey
  label: string
  /** 24x24 viewBox 的 path，和技能工作台现有图标风格一致 */
  path: string
  badge: number
}

const items = computed<MenuItem[]>(() => [
  {
    key: 'mine',
    label: '我的技能',
    path: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
    badge: 0,
  },
  {
    key: 'received',
    label: '收到的分享',
    path: 'M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4',
    badge: props.receivedCount,
  },
  {
    key: 'sent',
    label: '我的分享',
    path: 'M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 20V8m0 0l4 4m-4-4l-4 4',
    badge: props.sentPendingCount,
  },
])

function badgeText(value: number): string {
  if (value <= 0) return ''
  return value > 99 ? '99+' : String(value)
}

function select(key: PersonalMenuKey) {
  if (key !== props.active) emit('select', key)
}
</script>

<template>
  <!-- 宽屏：左侧栏 -->
  <aside
    v-if="variant === 'aside'"
    class="hidden w-[200px] shrink-0 border-r border-gray-100 bg-gray-50/70 p-4 md:block dark:border-gray-800 dark:bg-gray-800/70"
  >
    <div class="mb-5 flex items-center gap-2 px-2 text-sm font-bold text-gray-900 dark:text-gray-100">
      <span class="grid h-8 w-8 place-items-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-500/20">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h10" />
        </svg>
      </span>
      个人技能
    </div>
    <nav class="space-y-1">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="flex w-full cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-all"
        :class="active === item.key
          ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20'
          : 'text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-gray-100'"
        @click="select(item.key)"
      >
        <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" :d="item.path" />
        </svg>
        <span class="min-w-0 flex-1 truncate">{{ item.label }}</span>
        <span
          v-if="badgeText(item.badge)"
          class="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
          :class="active === item.key ? 'bg-white/20 text-white' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'"
        >{{ badgeText(item.badge) }}</span>
      </button>
    </nav>
  </aside>

  <!-- 窄屏：横向胶囊 -->
  <div v-else class="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
      :class="active === item.key
        ? 'border-blue-600 bg-blue-600 text-white shadow-sm'
        : 'border-gray-200 bg-white text-gray-600 hover:border-blue-200 hover:text-blue-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-blue-800 dark:hover:text-blue-400'"
      @click="select(item.key)"
    >
      <svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" :d="item.path" />
      </svg>
      <span>{{ item.label }}</span>
      <span
        v-if="badgeText(item.badge)"
        class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
        :class="active === item.key ? 'bg-white/20 text-white' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'"
      >{{ badgeText(item.badge) }}</span>
    </button>
  </div>
</template>
