<script setup lang="ts">
import { computed } from 'vue'

/**
 * 「我的技能」内的二级菜单：我的技能 / 收到的分享 / 我的分享。
 *
 * 照搬「我的数据门户」的左栏结构（200px、分组标题、选中项蓝底白字）；
 * 窄屏渲染为横向胶囊 Tab，避免在个人中心里再叠一层底部导航。
 */

type PersonalMenuKey = 'mine' | 'received' | 'sent'

/** 折叠状态归父级：宽度变化会影响右侧内容区，且需要跨刷新记住。 */
const props = withDefaults(defineProps<{
  active: PersonalMenuKey
  receivedCount?: number
  sentPendingCount?: number
  variant?: 'aside' | 'chips' | 'tabs'
  /** 仅 `aside` 变体生效：收成 64px 图标条，给内容区让出 136px */
  collapsed?: boolean
}>(), {
  receivedCount: 0,
  sentPendingCount: 0,
  variant: 'aside',
  collapsed: false,
})

const emit = defineEmits<{
  (e: 'select', key: PersonalMenuKey): void
  (e: 'toggle-collapsed'): void
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
  // 收件箱 / 纸飞机：与「我的 MCP」左栏保持同一套图标。
  // 原先用的托盘 + 上下箭头读起来像「导出 / 导入文件」，跟「谁把技能分享给了谁」无关。
  // 纸飞机用水平朝右那版：斜着的那版在 16px 下看着像三角警示牌。
  {
    key: 'received',
    label: '收到的分享',
    path: 'M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4',
    badge: props.receivedCount,
  },
  {
    key: 'sent',
    label: '我的分享',
    path: 'M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5',
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
  <!-- 宽屏：左侧栏（可折叠成图标条，给右侧内容区让出空间） -->
  <aside
    v-if="variant === 'aside'"
    class="hidden shrink-0 border-r border-gray-100 bg-gray-50/70 transition-[width,padding] duration-200 md:block dark:border-gray-800 dark:bg-gray-800/70"
    :class="collapsed ? 'w-[64px] p-2' : 'w-[200px] p-4'"
  >
    <div
      class="flex items-center gap-2"
      :class="collapsed ? 'mb-3 flex-col' : 'mb-5 px-2'"
    >
      <span class="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-500/20">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h10" />
        </svg>
      </span>
      <span v-if="!collapsed" class="min-w-0 flex-1 truncate text-sm font-bold text-gray-900 dark:text-gray-100">个人技能</span>
      <button
        type="button"
        class="grid h-6 w-6 shrink-0 cursor-pointer place-items-center rounded-lg text-gray-400 transition-colors hover:bg-white hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
        :title="collapsed ? '展开侧栏' : '收起侧栏'"
        :aria-expanded="!collapsed"
        aria-controls="skill-personal-menu"
        :aria-label="collapsed ? '展开个人技能侧栏' : '收起个人技能侧栏'"
        @click="emit('toggle-collapsed')"
      >
        <!-- 收起时指向右、展开时指向左：图标方向 = 点下去之后侧栏会往哪走 -->
        <svg
          class="h-4 w-4 transition-transform"
          :class="collapsed ? 'rotate-180' : ''"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
      </button>
    </div>
    <nav id="skill-personal-menu" class="space-y-1">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="flex w-full cursor-pointer items-center gap-2.5 rounded-xl text-sm transition-all"
        :class="[
          collapsed ? 'justify-center px-0 py-2.5' : 'px-3 py-2.5 text-left',
          active === item.key
            ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20'
            : 'text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-gray-100',
        ]"
        :title="collapsed ? item.label : undefined"
        :aria-label="collapsed ? item.label : undefined"
        @click="select(item.key)"
      >
        <!-- 折叠后只剩图标：角标改成一个点，避免把 64px 又撑开 -->
        <span class="relative shrink-0">
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" :d="item.path" />
          </svg>
          <span
            v-if="collapsed && badgeText(item.badge)"
            class="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ring-2"
            :class="active === item.key ? 'bg-white ring-blue-600' : 'bg-amber-500 ring-gray-50 dark:ring-gray-800'"
          ></span>
        </span>
        <template v-if="!collapsed">
          <span class="min-w-0 flex-1 truncate">{{ item.label }}</span>
          <span
            v-if="badgeText(item.badge)"
            class="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
            :class="active === item.key ? 'bg-white/20 text-white' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'"
          >{{ badgeText(item.badge) }}</span>
        </template>
      </button>
    </nav>
  </aside>

  <!-- 只有一个一级 Tab：二级视图直接作为顶部 tab（与「智能体中心」同款） -->
  <div
    v-else-if="variant === 'tabs'"
    class="flex flex-wrap items-center gap-1.5 border-b border-gray-200 pb-2"
    role="tablist"
    aria-label="技能视图"
  >
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      role="tab"
      :aria-selected="active === item.key"
      class="cursor-pointer rounded-lg px-3 py-1.5 text-sm font-medium transition-colors"
      :class="active === item.key ? 'bg-primary/10 text-primary' : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-white/5'"
      @click="select(item.key)"
    >
      <svg class="mr-1 inline-block h-3.5 w-3.5 shrink-0 align-text-bottom" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" :d="item.path" />
      </svg>
      <span>{{ item.label }}</span>
      <span
        v-if="badgeText(item.badge)"
        class="ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
        :class="active === item.key ? 'bg-primary/15 text-primary' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'"
      >{{ badgeText(item.badge) }}</span>
    </button>
  </div>

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
