<template>
  <!-- 外链且在“新标签打开”模式下必须是普通 a 标签：router-link 会把外站地址拼到本站路由上 -->
  <a
    v-if="opensInNewTab"
    :href="node.url || '#'"
    target="_blank"
    rel="noopener noreferrer"
    :title="titleAttr"
    :class="linkClass"
    data-menu-external="new_tab"
  >
    <MenuIcon :name="node.icon" :class="iconClass" />
    <template v-if="!collapsed">
      <span class="truncate leading-tight" :class="textClass">{{ node.label }}</span>
      <span class="ml-auto flex-shrink-0 text-[10px] opacity-50" aria-hidden="true">↗</span>
    </template>
    <span v-else class="sr-only">（外部链接，新标签打开）</span>
  </a>

  <router-link
    v-else
    :to="target"
    :title="titleAttr"
    :class="linkClass"
    :data-menu-external="isEmbedExternal ? 'embed' : undefined"
  >
    <MenuIcon :name="node.icon" :class="iconClass" />
    <template v-if="!collapsed">
      <span class="truncate leading-tight" :class="textClass">{{ node.label }}</span>
      <span v-if="isEmbedExternal" class="ml-auto flex-shrink-0 text-[10px] opacity-50" aria-hidden="true">▣</span>
    </template>
  </router-link>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MenuIcon from './MenuIcon.vue'
import type { NavNode } from '../../composables/useNavigation'

/**
 * 侧栏单个菜单项。
 *
 * 三种形态统一在这里处理，避免 `Dashboard.vue` 的模板里再分叉：
 * - 内置页面：`router-link` 到 `node.path`
 * - 外链 + 新标签：`a[target=_blank]`
 * - 外链 + 内嵌：`router-link` 到 `/dashboard/external/<code>` 容器页
 */
const props = defineProps<{
  node: NavNode
  collapsed: boolean
  mobile: boolean
  active: boolean
  lightTheme: boolean
}>()

const isEmbedExternal = computed(
  () => props.node.node_type === 'external' && props.node.open_mode === 'embed',
)
const opensInNewTab = computed(
  () => props.node.node_type === 'external' && !isEmbedExternal.value,
)

const target = computed(() => {
  if (isEmbedExternal.value) return `/dashboard/external/${props.node.code}`
  return props.node.path || '/dashboard'
})

const titleAttr = computed(() => {
  if (!props.collapsed) return undefined
  return opensInNewTab.value ? `${props.node.label}（新标签打开）` : props.node.label
})

const linkClass = computed(() => {
  const base = 'group relative flex items-center font-medium transition-all duration-150 whitespace-nowrap'
  const state = props.active
    ? props.lightTheme
      ? 'bg-black/[0.05] text-gray-900 font-medium border border-black/[0.05]'
      : 'bg-white/[0.08] text-white font-medium border border-white/[0.08]'
    : props.lightTheme
      ? 'text-gray-500 hover:bg-black/[0.03] hover:text-gray-900 border border-transparent'
      : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200 border border-transparent'
  const size = props.collapsed
    ? 'justify-center p-2.5 mx-auto w-10 h-10 rounded-lg text-sm'
    : props.mobile
      ? 'py-2.5 px-3.5 mx-1 rounded-xl text-[15px]'
      : 'py-2 px-3 mx-1 rounded-lg text-sm'
  return `${base} ${state} ${size}`
})

const iconClass = computed(() => {
  const size = 'h-[18px] w-[18px]'
  if (props.active) return `${size} ${props.lightTheme ? 'text-gray-900' : 'text-white'}`
  return `${size} ${props.lightTheme ? 'text-gray-400 group-hover:text-gray-600' : 'text-slate-400 group-hover:text-slate-200'}`
})

const textClass = computed(() => (props.mobile ? 'ml-3 text-[15px]' : 'ml-2.5 text-sm'))
</script>
