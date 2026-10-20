<template>
  <div>
    <p
      v-if="isEmpty"
      class="py-6 text-center text-xs italic text-gray-400"
    >（暂无授权）</p>

    <template v-else>
      <p class="mb-1.5 px-1 text-[10px] leading-tight text-gray-400">
        界面菜单与页面内功能点按菜单层级展示；浅灰「仅分组」表示您只有其下某项权限，本身没有该菜单入口。
      </p>

      <div class="space-y-1">
        <PersonalPermissionTreeNode
          v-for="node in view.nodes"
          :key="node.id"
          :node="node"
          :depth="0"
          :collapsed-ids="collapsedIds"
          @toggle-collapse="toggleCollapse"
        />
      </div>

      <div v-if="unmatchedItems.length" class="mt-3 border-t border-dashed border-gray-200 pt-2">
        <p class="mb-1.5 px-1 text-[10px] font-bold text-gray-400">其他（未在菜单树中登记）</p>
        <div class="flex flex-wrap gap-1.5 px-1">
          <span
            v-for="item in unmatchedItems"
            :key="item.id"
            class="rounded border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-bold text-gray-600"
            :title="item.id"
          >{{ item.label }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PermissionNode } from '../../constants/permissions'
import { buildGrantedPermissionView } from '../../utils/permissionTreeView'
import PersonalPermissionTreeNode from './PersonalPermissionTreeNode.vue'

/** 后端 details.menus / details.elements 的最小形状：只需要 id 与展示名。 */
interface PermissionDetailItem {
  id: string
  name?: string
  display_name?: string
}

const props = withDefaults(
  defineProps<{
    /** 菜单骨架（GET /api/portal/menus/catalog）：菜单已入库，组件不再内置数据 */
    tree?: PermissionNode[]
    /** 已授权的界面菜单（details.menus） */
    menus?: PermissionDetailItem[]
    /** 已授权的功能点（details.elements） */
    elements?: PermissionDetailItem[]
  }>(),
  { tree: () => [], menus: () => [], elements: () => [] },
)

const menuIds = computed(() =>
  props.menus.map((item) => (item?.id || '').trim()).filter(Boolean),
)
const elementIds = computed(() =>
  props.elements.map((item) => (item?.id || '').trim()).filter(Boolean),
)

const view = computed(() =>
  buildGrantedPermissionView(props.tree, menuIds.value, elementIds.value),
)

/** 后端展示名兜底：树里没登记的新权限码也要能读懂，不能只露一个 id。 */
const labelById = computed(() => {
  const map = new Map<string, string>()
  for (const item of [...props.menus, ...props.elements]) {
    const id = (item?.id || '').trim()
    if (!id) continue
    map.set(id, (item.display_name || item.name || id).trim())
  }
  return map
})

const unmatchedItems = computed(() =>
  [...view.value.unmatchedMenus, ...view.value.unmatchedElements].map((id) => ({
    id,
    label: labelById.value.get(id) || id,
  })),
)

const isEmpty = computed(() => !view.value.nodes.length && !unmatchedItems.value.length)

/** 折叠状态只活在本次打开期间，不持久化：默认全展开，先让人看全。 */
const collapsedIds = ref<string[]>([])

const toggleCollapse = (nodeId: string) => {
  collapsedIds.value = collapsedIds.value.includes(nodeId)
    ? collapsedIds.value.filter((id) => id !== nodeId)
    : [...collapsedIds.value, nodeId]
}
</script>
