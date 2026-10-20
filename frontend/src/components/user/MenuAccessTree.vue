<template>
  <div class="space-y-2">
    <!-- 级别说明：树形化后不再每条菜单重复标签，用一次图例讲清两级语义 -->
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[10px] text-gray-500">
      <span class="inline-flex items-center gap-1">
        <svg class="h-3.5 w-3.5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5" />
        </svg>
        <b class="font-bold text-blue-600">菜单权限</b>
        <span>勾选表示可访问该页面</span>
      </span>
      <span class="inline-flex items-center gap-1">
        <svg class="h-3.5 w-3.5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 5h6M9 9h6M9 13h6M9 17h4M5 5.5A1.5 1.5 0 016.5 4h11A1.5 1.5 0 0119 5.5v13a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 015 18.5v-13z" />
        </svg>
        <b class="font-bold text-indigo-500">功能点权限</b>
        <span>控制页面内按钮和操作</span>
      </span>
    </div>

    <div
      v-for="menu in MENUS"
      :key="menu.id"
      class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
    >
      <!-- 一级：菜单权限 -->
      <div
        class="flex items-center gap-2 px-2.5 py-2"
        :class="isMenuInheritedOnly(menu) ? 'bg-slate-50' : 'bg-gray-50'"
      >
        <button
          v-if="menu.children.length > 0"
          type="button"
          class="shrink-0 rounded p-0.5 text-gray-400 transition-colors hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          :aria-expanded="!isCollapsed(menu.id)"
          :aria-label="isCollapsed(menu.id) ? `展开「${menu.label}」` : `折叠「${menu.label}」`"
          @click.stop="toggleCollapsed(menu.id)"
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
          class="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded"
          role="checkbox"
          :aria-checked="isMenuChecked(menu)"
          :aria-disabled="disabled"
          :tabindex="disabled ? -1 : 0"
          @click="onMenuToggle(menu)"
          @keydown.space.prevent="onMenuToggle(menu)"
          @keydown.enter.prevent="onMenuToggle(menu)"
        >
          <input
            type="checkbox"
            class="pointer-events-none h-4 w-4 shrink-0 rounded border-gray-300 focus:ring-blue-500"
            :class="isMenuInheritedOnly(menu) ? 'accent-slate-400' : 'accent-blue-600'"
            :checked="isMenuChecked(menu)"
            :indeterminate="isMenuPartiallySelected(menu.id)"
            :disabled="disabled"
            tabindex="-1"
            aria-hidden="true"
          />
          <span
            class="min-w-0 truncate text-xs font-black sm:text-sm"
            :class="isMenuInheritedOnly(menu) ? 'text-slate-600' : 'text-gray-800'"
          >{{ menu.label }}</span>
          <span
            v-if="isMenuInheritedOnly(menu)"
            class="shrink-0 rounded-full bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold text-slate-600"
            title="该权限来自所属角色，需到角色管理调整"
          >角色</span>
        </div>

        <span v-if="menu.children.length > 0" class="shrink-0 text-[10px] text-gray-400">
          {{ menu.children.length }} 功能点
        </span>
      </div>

      <!-- 二级：功能点权限 -->
      <div
        v-if="menu.children.length > 0 && !isCollapsed(menu.id)"
        class="ml-4 space-y-0.5 border-l border-gray-200 py-1.5 pl-3 sm:ml-5 sm:pl-5"
      >
        <div
          v-for="child in menu.children"
          :key="child.id"
          class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left transition-colors"
          :class="[
            isInheritedOnly(child.id) ? 'bg-slate-50/60 hover:bg-slate-100' : 'hover:bg-blue-50',
            disabled ? 'cursor-not-allowed opacity-60' : '',
          ]"
          role="checkbox"
          :aria-checked="isGranted(child.id)"
          :aria-disabled="disabled"
          :tabindex="disabled ? -1 : 0"
          :title="isInheritedOnly(child.id) ? '该权限来自所属角色，需到角色管理调整' : child.label"
          @click="onElementToggle(child)"
          @keydown.space.prevent="onElementToggle(child)"
          @keydown.enter.prevent="onElementToggle(child)"
        >
          <input
            type="checkbox"
            class="pointer-events-none h-3.5 w-3.5 shrink-0 rounded border-gray-300 focus:ring-blue-500"
            :class="isInheritedOnly(child.id) ? 'accent-slate-400' : 'accent-blue-600'"
            :checked="isGranted(child.id)"
            :disabled="disabled"
            tabindex="-1"
            aria-hidden="true"
          />
          <span
            class="min-w-0 text-[11px] leading-tight"
            :class="isInheritedOnly(child.id) ? 'text-slate-500' : 'text-gray-600'"
          >{{ child.label }}</span>
          <span
            v-if="isInheritedOnly(child.id)"
            class="shrink-0 rounded-full bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold text-slate-600"
            title="该权限来自所属角色，需到角色管理调整"
          >角色</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { MENU_TREE } from '../../constants/permissions'

interface MenuChild {
  id: string
  label: string
}

interface MenuNode {
  id: string
  label: string
  children: MenuChild[]
}

const MENUS = MENU_TREE as unknown as MenuNode[]

const props = withDefaults(
  defineProps<{
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
  (e: 'inherited-blocked', itemId: string): void
}>()

/** 折叠状态只活在本次打开期间，不持久化：默认全展开，先让人看全。 */
const collapsed = ref<Set<string>>(new Set())

const isCollapsed = (menuId: string) => collapsed.value.has(menuId)

const toggleCollapsed = (menuId: string) => {
  const next = new Set(collapsed.value)
  if (next.has(menuId)) next.delete(menuId)
  else next.add(menuId)
  collapsed.value = next
}

// MENU_TREE 的子项混有两类 id：`menu:*` 子菜单（用户管理/角色管理/系统配置/审计日志）
// 与 `element:*` 功能点。判定必须两边都查，否则直配的子菜单会显示成空框。
const isDirect = (id: string) => props.menus.includes(id) || props.elements.includes(id)
const isInherited = (id: string) => props.inheritedMenus.includes(id) || props.inheritedElements.includes(id)
const isGranted = (id: string) => isDirect(id) || isInherited(id)
/** 只读态：没有直配、但有角色授予。 */
const isInheritedOnly = (id: string) => !isDirect(id) && isInherited(id)

const childIds = (menu: MenuNode) => (menu.children || []).map((child) => child.id)
const grantedChildCount = (menu: MenuNode) => childIds(menu).filter(isGranted).length

/** 视觉上的「已授予」：菜单本身被授予，或全部子项都已授予（直配或继承）。 */
const isMenuChecked = (menu: MenuNode) =>
  props.menus.includes(menu.id) ||
  props.inheritedMenus.includes(menu.id) ||
  (menu.children.length > 0 && grantedChildCount(menu) === menu.children.length)

const isMenuPartiallySelected = (menuId: string) => {
  const menu = MENUS.find((item) => item.id === menuId)
  if (!menu || menu.children.length === 0) return false
  const granted = grantedChildCount(menu)
  return granted > 0 && granted < menu.children.length && !isMenuChecked(menu)
}

/** 菜单本身与它的所有子项都没有直配、但至少有一项来自角色 → 整行只读。 */
const isMenuInheritedOnly = (menu: MenuNode) => {
  const hasDirect = props.menus.includes(menu.id) || childIds(menu).some(isDirect)
  if (hasDirect) return false
  return (
    props.inheritedMenus.includes(menu.id) ||
    (menu.children.length > 0 && menu.children.every((child) => isInherited(child.id)))
  )
}

const onMenuToggle = (menu: MenuNode) => {
  if (props.disabled) return
  if (isMenuInheritedOnly(menu)) {
    emit('inherited-blocked', menu.id)
    return
  }
  emit('toggle-menu', menu.id)
}

const onElementToggle = (child: MenuChild) => {
  if (props.disabled) return
  if (isInheritedOnly(child.id)) {
    emit('inherited-blocked', child.id)
    return
  }
  emit('toggle-element', child.id)
}
</script>
