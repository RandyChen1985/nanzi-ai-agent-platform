<template>
  <div class="space-y-2">
    <!-- 三级图例：菜单 → 页面（子菜单）→ 功能点 -->
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[10px] text-gray-500">
      <span class="inline-flex items-center gap-1">
        <svg class="h-3.5 w-3.5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5" />
        </svg>
        <b class="font-bold text-blue-600">菜单权限</b>
        <span>侧栏入口</span>
      </span>
      <span class="inline-flex items-center gap-1">
        <svg class="h-3.5 w-3.5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 4h7l4 4v12a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1zM13 4v5h5" />
        </svg>
        <b class="font-bold text-indigo-500">页面</b>
        <span>能点进去的子菜单</span>
      </span>
      <span class="inline-flex items-center gap-1">
        <svg class="h-3.5 w-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 5h6M9 9h6M9 13h6M9 17h4M5 5.5A1.5 1.5 0 016.5 4h11A1.5 1.5 0 0119 5.5v13a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 015 18.5v-13z" />
        </svg>
        <b class="font-bold text-slate-500">功能点权限</b>
        <span>页面内按钮与操作</span>
      </span>
    </div>
    <p class="px-1 text-[10px] leading-tight text-gray-400">
      勾菜单只授予入口，功能点需单独勾选；菜单行的「全选」可一次性授予其下全部功能点。
    </p>

    <div
      v-for="menu in tree"
      :key="menu.id"
      class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
    >
      <!-- 一级：菜单（侧栏入口） -->
      <div
        class="flex items-center gap-2 px-2.5 py-2"
        :class="isSelfInherited(menu) ? 'bg-slate-50' : 'bg-gray-50'"
      >
        <button
          v-if="hasChildren(menu)"
          type="button"
          class="shrink-0 rounded p-0.5 text-gray-400 transition-colors hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          :aria-expanded="!isCollapsed(menu.id)"
          :aria-label="isCollapsed(menu.id) ? `展开「${menu.label}」` : `折叠「${menu.label}」`"
          @click.stop="toggleCollapse(menu.id)"
        >
          <svg
            class="h-3.5 w-3.5 transition-transform"
            :class="isCollapsed(menu.id) ? '' : 'rotate-90'"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>
        <span v-else class="w-4 shrink-0"></span>

        <div
          class="flex min-w-0 flex-1 cursor-pointer select-none items-center gap-2 rounded"
          :role="isGroupNode(menu) ? 'button' : 'checkbox'"
          :aria-checked="isGroupNode(menu) ? undefined : (isSelfGranted(menu) ? 'true' : isPartial(menu) ? 'mixed' : 'false')"
          :aria-disabled="isGroupNode(menu) ? undefined : disabled"
          :tabindex="disabled && !isGroupNode(menu) ? -1 : 0"
          :title="headerTitle(menu)"
          @click="onHeaderClick(menu)"
          @keydown.space.prevent="onHeaderClick(menu)"
          @keydown.enter.prevent="onHeaderClick(menu)"
        >
          <span v-if="isGroupNode(menu)" class="flex h-4 w-4 shrink-0 items-center justify-center text-gray-400">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </span>
          <input
            v-else
            type="checkbox"
            class="pointer-events-none h-4 w-4 shrink-0 rounded border-gray-300 focus:ring-blue-500"
            :class="isSelfInherited(menu) ? 'accent-slate-400' : 'accent-blue-600'"
            :checked="isSelfGranted(menu)"
            :indeterminate="isPartial(menu)"
            :disabled="disabled"
            tabindex="-1"
            aria-hidden="true"
          />
          <svg
            v-if="!isGroupNode(menu)"
            class="h-4 w-4 shrink-0"
            :class="isSelfInherited(menu) ? 'text-slate-400' : 'text-blue-600'"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5" />
          </svg>
          <span
            class="min-w-0 truncate text-xs font-black sm:text-sm"
            :class="isSelfInherited(menu) ? 'text-slate-600' : 'text-gray-800'"
          >{{ menu.label }}</span>
          <span
            v-if="isGroupNode(menu)"
            class="shrink-0 rounded-full bg-gray-200 px-1.5 py-0.5 text-[9px] font-bold text-gray-500"
            title="分组只作展示容器，本身不可勾选"
          >分组</span>
          <span
            v-else-if="isSelfInherited(menu)"
            class="shrink-0 rounded-full bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold text-slate-600"
            title="该权限来自所属角色，需到角色管理调整"
          >角色</span>
        </div>

        <template v-if="hasChildren(menu)">
          <span class="shrink-0 text-[10px] text-gray-400">
            功能点 {{ grantedLeafCount(menu) }}/{{ countLeafElements(menu) }}
          </span>
          <button
            type="button"
            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-blue-600 transition-colors hover:bg-blue-100 disabled:opacity-40"
            :disabled="disabled"
            :title="isFullyGranted(menu) ? `清空「${menu.label}」下的全部功能点` : `授予「${menu.label}」下的全部功能点`"
            @click.stop="onMenuCascade(menu)"
          >{{ isFullyGranted(menu) ? '清空' : '全选' }}</button>
        </template>
      </div>

      <!-- 二级及以下：子菜单（页面）与功能点，递归渲染 -->
      <div
        v-if="hasChildren(menu) && !isCollapsed(menu.id)"
        class="border-l border-gray-200"
        style="margin-left: 14px"
      >
        <MenuTreeNode
          v-for="child in menu.children"
          :key="child.id"
          :node="child"
          :depth="0"
          :menus="menus"
          :elements="elements"
          :inherited-menus="inheritedMenus"
          :inherited-elements="inheritedElements"
          :disabled="disabled"
          :collapsed-ids="collapsedIds"
          @toggle-menu="forwardToggleMenu"
          @toggle-element="forwardToggleElement"
          @toggle-menu-descendants="forwardToggleMenuDescendants"
          @inherited-blocked="forwardInheritedBlocked"
          @toggle-collapse="toggleCollapse"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { PermissionNode } from '../../constants/permissions'
import {
  countGrantedLeafElements,
  countLeafElements,
  isPermissionNodeGranted,
  isPermissionNodePartiallySelected,
  isPermissionNodeSelfGranted,
  isPermissionNodeSelfInherited,
  type MenuCascadeMode,
  type PermissionGrantSets,
} from '../../utils/permissionLists'
import MenuTreeNode from './MenuTreeNode.vue'

const props = withDefaults(
  defineProps<{
    /** 权限树（由父层从菜单接口取回后传入；菜单已入库，组件不再内置数据） */
    tree: PermissionNode[]
    /** 用户直配的菜单（唯一可提交集合） */
    menus: string[]
    /** 用户直配的功能点（唯一可提交集合） */
    elements: string[]
    /** 角色授予的菜单（只读） */
    inheritedMenus: string[]
    /** 角色授予的功能点（只读） */
    inheritedElements: string[]
    /** 权限未成功加载时整体禁用 */
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  (e: 'toggle-menu', menuId: string): void
  (e: 'toggle-element', elementId: string): void
  (e: 'toggle-menu-descendants', menuId: string, mode: MenuCascadeMode): void
  (e: 'inherited-blocked', itemId: string): void
}>()

/** 折叠状态只活在本次打开期间，不持久化：默认全展开，先让人看全。 */
const collapsedIds = ref<string[]>([])

const isCollapsed = (nodeId: string) => collapsedIds.value.includes(nodeId)

const toggleCollapse = (nodeId: string) => {
  const index = collapsedIds.value.indexOf(nodeId)
  if (index >= 0) collapsedIds.value = collapsedIds.value.filter((id) => id !== nodeId)
  else collapsedIds.value = [...collapsedIds.value, nodeId]
}

const grantSets = (): PermissionGrantSets => ({
  menus: props.menus,
  elements: props.elements,
  inheritedMenus: props.inheritedMenus,
  inheritedElements: props.inheritedElements,
})

const hasChildren = (node: PermissionNode) => (node.children || []).length > 0

/**
 * 分组节点（「系统管理」「智能体开发平台」…）。
 *
 * 分组在侧栏里是一级标题，但在权限树里只是展示容器：它没有可授权语义，
 * 勾选只会往授权表写一条 `group:*` 垃圾行。因此分组不渲染复选框，
 * 点标题只折叠/展开，批量授权走它右侧的「全选」。
 */
const isGroupNode = (node: PermissionNode) => node.id.startsWith('group:')

const headerTitle = (node: PermissionNode) => {
  if (isGroupNode(node)) return '分组只作展示容器，本身不可勾选；可点右侧「全选」授予其下全部功能点'
  if (isSelfInherited(node)) return '该权限来自所属角色，需到角色管理调整'
  return `${node.label}：勾选只授予菜单入口；取消会一并清空其下已配的功能点`
}

const onHeaderClick = (menu: PermissionNode) => {
  if (isGroupNode(menu)) {
    toggleCollapse(menu.id)
    return
  }
  onMenuToggle(menu)
}

/** 复选框只看节点自身：勾菜单 = 授予入口，不再代表整棵子树。 */
const isSelfGranted = (node: PermissionNode) => isPermissionNodeSelfGranted(node, grantSets())
/** 整棵子树是否全部授予，决定「全选 / 清空」按钮的文案。 */
const isFullyGranted = (node: PermissionNode) => isPermissionNodeGranted(node, grantSets())
/** 半选只出现在「自身未勾、但有后代已授予」时（含继承与历史脏数据）。 */
const isPartial = (node: PermissionNode) =>
  !isSelfGranted(node) && isPermissionNodePartiallySelected(node, grantSets())
/** 自身只由角色授予 → 该行只读；后代各自判定，不被父级锁死。 */
const isSelfInherited = (node: PermissionNode) =>
  isPermissionNodeSelfInherited(node, grantSets())

const grantedLeafCount = (node: PermissionNode) => countGrantedLeafElements(node, grantSets())

// 子节点的事件重新向外 emit，显式转发，避免在模板里写隐式 any 的箭头函数
const forwardToggleMenu = (id: string) => emit('toggle-menu', id)
const forwardToggleElement = (id: string) => emit('toggle-element', id)
const forwardToggleMenuDescendants = (id: string, mode: MenuCascadeMode) =>
  emit('toggle-menu-descendants', id, mode)
const forwardInheritedBlocked = (id: string) => emit('inherited-blocked', id)

const onMenuToggle = (menu: PermissionNode) => {
  if (props.disabled) return
  if (isSelfInherited(menu)) {
    emit('inherited-blocked', menu.id)
    return
  }
  emit('toggle-menu', menu.id)
}

const onMenuCascade = (menu: PermissionNode) => {
  if (props.disabled) return
  emit('toggle-menu-descendants', menu.id, isFullyGranted(menu) ? 'clear' : 'select')
}
</script>
