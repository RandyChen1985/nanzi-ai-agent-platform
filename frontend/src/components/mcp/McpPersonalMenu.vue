<script setup lang="ts">
/**
 * 「我的 MCP」内的二级菜单：我的 MCP / 收到的分享 / 我的分享。
 *
 * 与技能分享（SkillPersonalMenu）同构：宽屏左侧栏，窄屏折叠为横向胶囊。
 */
import { computed } from 'vue'
import type { McpPersonalMenuKey } from '@/utils/mcpShare'

const props = withDefaults(defineProps<{
  active: McpPersonalMenuKey
  counts?: { received_pending: number; sent_pending: number; sent_total: number }
  variant?: 'aside' | 'chips'
}>(), {
  counts: () => ({ received_pending: 0, sent_pending: 0, sent_total: 0 }),
  variant: 'aside',
})

const emit = defineEmits<{ (e: 'select', key: McpPersonalMenuKey): void }>()

const items = computed(() => [
  {
    key: 'mine' as const,
    label: '我的 MCP',
    path: 'M5 12h14M12 5l7 7-7 7',
    badge: null as number | null,
  },
  {
    key: 'received' as const,
    label: '收到的分享',
    path: 'M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4',
    badge: props.counts?.received_pending || null,
  },
  {
    key: 'sent' as const,
    label: '我的分享',
    path: 'M12 19l9 2-9-18-9 18 9-2zm0 0v-8',
    badge: props.counts?.sent_pending || null,
  },
])

const badgeText = (value: number | null): string =>
  value && value > 0 ? (value > 99 ? '99+' : String(value)) : ''
</script>

<template>
  <!-- 宽屏：左侧栏 -->
  <aside
    v-if="variant === 'aside'"
    class="hidden w-[200px] shrink-0 border-r border-gray-100 bg-slate-50/70 p-4 md:block dark:border-gray-800 dark:bg-gray-800/70"
  >
    <div class="mb-5 flex items-center gap-2 px-2 text-sm font-bold text-gray-900 dark:text-gray-100">
      <span class="grid h-8 w-8 place-items-center rounded-xl bg-emerald-600 text-white shadow-sm shadow-emerald-500/20">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7" />
        </svg>
      </span>
      个人 MCP
    </div>
    <nav class="space-y-1">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="flex w-full cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-all"
        :class="active === item.key
          ? 'bg-emerald-600 font-semibold text-white shadow-sm shadow-emerald-500/20'
          : 'text-gray-600 hover:bg-white hover:text-emerald-700 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-emerald-300'"
        @click="emit('select', item.key)"
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
  <div v-else class="flex gap-2 overflow-x-auto pb-1 md:hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
      :class="active === item.key
        ? 'border-emerald-600 bg-emerald-600 text-white shadow-sm'
        : 'border-gray-200 bg-white text-gray-600 hover:border-emerald-200 hover:text-emerald-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-emerald-800 dark:hover:text-emerald-400'"
      @click="emit('select', item.key)"
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
