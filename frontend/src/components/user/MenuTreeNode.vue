<template>
  <div>
    <!-- 节点行：菜单（页面）与功能点共用一套结构，靠视觉层级区分 -->
    <div
      class="flex items-center gap-2 rounded transition-colors"
      :class="[inheritedOnly ? 'bg-slate-50' : 'hover:bg-blue-50/60', disabled ? 'opacity-60' : '']"
      :style="{ paddingLeft: `${12 + depth * 16}px`, paddingRight: '8px' }"
    >
      <button
        v-if="hasChildren"
        type="button"
        class="shrink-0 rounded p-0.5 text-gray-400 transition-colors hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        :aria-expanded="!isCollapsed"
        :aria-label="isCollapsed ? `展开「${node.label}」` : `折叠「${node.label}」`"
        @click.stop="emit('toggle-collapse', node.id)"
      >
        <svg
          class="h-3 w-3 transition-transform"
          :class="isCollapsed ? '' : 'rotate-90'"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>
      <span v-else class="w-3 shrink-0"></span>

      <div
        class="flex min-w-0 flex-1 cursor-pointer select-none items-center gap-2 py-1.5"
        role="checkbox"
        :aria-checked="isGranted"
        :aria-disabled="disabled"
        :tabindex="disabled ? -1 : 0"
        :title="inheritedOnly ? '该权限来自所属角色，需到角色管理调整' : node.label"
        @click="onToggle"
        @keydown.space.prevent="onToggle"
        @keydown.enter.prevent="onToggle"
      >
        <input
          type="checkbox"
          class="pointer-events-none h-3.5 w-3.5 shrink-0 rounded border-gray-300 focus:ring-blue-500"
          :class="inheritedOnly ? 'accent-slate-400' : 'accent-blue-600'"
          :checked="isGranted"
          :indeterminate="isPartial"
          :disabled="disabled"
          tabindex="-1"
          aria-hidden="true"
        />
        <svg
          v-if="isMenu"
          class="h-3.5 w-3.5 shrink-0"
          :class="inheritedOnly ? 'text-slate-400' : 'text-indigo-500'"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5" />
        </svg>
        <span
          class="min-w-0 truncate leading-tight"
          :class="[
            isMenu ? 'text-[11px] font-bold' : 'text-[11px]',
            inheritedOnly ? 'text-slate-500' : isMenu ? 'text-gray-800' : 'text-gray-600',
          ]"
        >{{ node.label }}</span>
        <span
          v-if="isMenu"
          class="shrink-0 rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-bold text-indigo-500"
          title="可以点进去的页面"
        >页面</span>
        <span
          v-if="inheritedOnly"
          class="shrink-0 rounded-full bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold text-slate-600"
          title="该权限来自所属角色，需到角色管理调整"
        >角色</span>
      </div>
    </div>

    <!-- 子节点：用竖线表达从属关系 -->
    <div
      v-if="hasChildren && !isCollapsed"
      class="border-l border-gray-200"
      :style="{ marginLeft: `${14 + depth * 16}px` }"
    >
      <MenuTreeNode
        v-for="child in children"
        :key="child.id"
        :node="child"
        :depth="depth + 1"
        :menus="menus"
        :elements="elements"
        :inherited-menus="inheritedMenus"
        :inherited-elements="inheritedElements"
        :disabled="disabled"
        :collapsed-ids="collapsedIds"
        @toggle-menu="forwardToggleMenu"
        @toggle-element="forwardToggleElement"
        @inherited-blocked="forwardInheritedBlocked"
        @toggle-collapse="forwardToggleCollapse"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PermissionNode } from '../../constants/permissions'
import {
  hasDirectGrantIn,
  hasInheritedGrantIn,
  isPermissionNodeGranted,
  isPermissionNodePartiallySelected,
  type PermissionGrantSets,
} from '../../utils/permissionLists'

defineOptions({ name: 'MenuTreeNode' })

const props = withDefaults(
  defineProps<{
    node: PermissionNode
    /** 0 = 卡片里的第一层（子菜单或功能点），每深一层缩进加 16px */
    depth: number
    menus: string[]
    elements: string[]
    inheritedMenus: string[]
    inheritedElements: string[]
    disabled?: boolean
    collapsedIds: string[]
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  (e: 'toggle-menu', menuId: string): void
  (e: 'toggle-element', elementId: string): void
  (e: 'inherited-blocked', itemId: string): void
  (e: 'toggle-collapse', nodeId: string): void
}>()

const isMenu = computed(() => props.node.id.startsWith('menu:'))
const children = computed(() => props.node.children || [])
const hasChildren = computed(() => children.value.length > 0)
const isCollapsed = computed(() => props.collapsedIds.includes(props.node.id))

const grantSets = computed<PermissionGrantSets>(() => ({
  menus: props.menus,
  elements: props.elements,
  inheritedMenus: props.inheritedMenus,
  inheritedElements: props.inheritedElements,
}))

const isGranted = computed(() => isPermissionNodeGranted(props.node, grantSets.value))
const isPartial = computed(() => isPermissionNodePartiallySelected(props.node, grantSets.value))
/** 自己和整棵子树都没有直配、但至少有一项来自角色 → 整行只读。 */
const inheritedOnly = computed(
  () => !hasDirectGrantIn(props.node, grantSets.value) && hasInheritedGrantIn(props.node, grantSets.value),
)

// 递归子节点要重新 emit，显式转发一次，避免在模板里写带类型注解的箭头函数
const forwardToggleMenu = (id: string) => emit('toggle-menu', id)
const forwardToggleElement = (id: string) => emit('toggle-element', id)
const forwardInheritedBlocked = (id: string) => emit('inherited-blocked', id)
const forwardToggleCollapse = (id: string) => emit('toggle-collapse', id)

const onToggle = () => {
  if (props.disabled) return
  if (inheritedOnly.value) {
    emit('inherited-blocked', props.node.id)
    return
  }
  if (isMenu.value) emit('toggle-menu', props.node.id)
  else emit('toggle-element', props.node.id)
}
</script>
