<template>
  <div class="space-y-0.5">
    <div v-if="!flatNodes.length" class="px-3 py-6 text-center text-sm text-gray-400">
      还没有部门，点上方「新建部门」开始
    </div>

    <div
      v-for="row in flatNodes"
      :key="row.node.id"
      class="group flex cursor-pointer items-center gap-1 rounded-lg py-1.5 pr-1 text-sm transition-colors"
      :class="
        row.node.id === selectedId
          ? 'bg-blue-50 font-bold text-blue-700 dark:bg-blue-500/10 dark:text-blue-300'
          : 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800'
      "
      :style="{ paddingLeft: `${6 + row.depth * 14}px` }"
      @click="emit('select', row.node)"
    >
      <button
        v-if="row.hasChildren"
        type="button"
        class="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
        :title="isExpanded(row.node.id) ? '收起' : '展开'"
        @click.stop="emit('toggle-expand', row.node.id)"
      >
        <svg
          class="h-3 w-3 transition-transform"
          :class="isExpanded(row.node.id) ? 'rotate-90' : ''"
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fill-rule="evenodd"
            d="M7.21 14.77a.75.75 0 01.02-1.06L11.17 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
            clip-rule="evenodd"
          />
        </svg>
      </button>
      <span v-else class="w-4 flex-shrink-0" />

      <span class="min-w-0 truncate">{{ row.node.name }}</span>
      <span
        class="ml-1 flex-shrink-0 rounded-full bg-gray-100 px-1.5 text-[11px] font-normal text-gray-500 dark:bg-gray-700 dark:text-gray-300"
        :title="`直属成员 ${row.node.member_count} 人`"
      >{{ row.node.member_count }}</span>
      <span
        v-if="row.node.status === 0"
        class="flex-shrink-0 rounded bg-amber-50 px-1.5 text-[11px] font-normal text-amber-600 dark:bg-amber-500/10 dark:text-amber-300"
      >停用</span>

      <div
        v-if="!readonly"
        class="ml-auto hidden flex-shrink-0 items-center gap-0.5 group-hover:flex"
      >
        <button
          type="button"
          class="rounded px-1 text-sm text-gray-400 hover:bg-white hover:text-blue-600 dark:hover:bg-gray-900"
          title="新建子部门"
          @click.stop="emit('action', { type: 'create-child', node: row.node })"
        >+</button>
        <button
          type="button"
          class="rounded px-1 text-sm text-gray-400 hover:bg-white hover:text-gray-700 dark:hover:bg-gray-900"
          title="上移"
          @click.stop="emit('action', { type: 'move-up', node: row.node })"
        >↑</button>
        <button
          type="button"
          class="rounded px-1 text-sm text-gray-400 hover:bg-white hover:text-gray-700 dark:hover:bg-gray-900"
          title="下移"
          @click.stop="emit('action', { type: 'move-down', node: row.node })"
        >↓</button>
        <button
          v-if="canDelete"
          type="button"
          class="rounded px-1 text-sm text-gray-400 hover:bg-white hover:text-red-600 dark:hover:bg-gray-900"
          title="删除部门"
          @click.stop="emit('action', { type: 'delete', node: row.node })"
        >✕</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 部门树（扁平渲染 + 缩进）。
 *
 * 刻意不用递归组件：树只有单向展示需求，拍平后事件不必逐层透传，也便于在每行做
 * hover 操作与"直属人数"徽标。展开状态由父级持有（刷新后仍保持）。
 */
export interface DeptNode {
  id: number
  parent_id: number | null
  code: string
  name: string
  path: string
  sort_order: number
  status: number
  description: string | null
  member_count: number
  children: DeptNode[]
}

const props = withDefaults(
  defineProps<{
    nodes: DeptNode[]
    selectedId?: number | null
    expandedIds?: number[]
    readonly?: boolean
    /** 删除权限与编辑权限是两码事：只给编辑权的人不该看到 ✕（点了必然 403） */
    canDelete?: boolean
  }>(),
  { selectedId: null, expandedIds: () => [], readonly: false, canDelete: false }
)

const emit = defineEmits<{
  (e: 'select', node: DeptNode): void
  (e: 'toggle-expand', id: number): void
  (e: 'action', payload: { type: string; node: DeptNode }): void
}>()

const expandedSet = computed(() => new Set(props.expandedIds || []))

const isExpanded = (id: number) => expandedSet.value.has(id)

const flatNodes = computed(() => {
  const rows: { node: DeptNode; depth: number; hasChildren: boolean }[] = []
  const walk = (list: DeptNode[], depth: number) => {
    for (const node of list) {
      const children = node.children || []
      rows.push({ node, depth, hasChildren: children.length > 0 })
      // 默认展开：未在 expandedIds 里显式记录的节点也展开，避免初次进入看到一棵"折起来"的树
      if (children.length && (!props.expandedIds?.length || isExpanded(node.id))) {
        walk(children, depth + 1)
      }
    }
  }
  walk(props.nodes || [], 0)
  return rows
})
</script>
