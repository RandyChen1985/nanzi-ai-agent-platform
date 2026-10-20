<template>
  <div>
    <!-- 只读节点行：菜单 / 子菜单 / 功能点共用一套结构，靠缩进与图标区分层级 -->
    <div
      class="flex items-center gap-1.5 rounded-md pr-2 transition-colors"
      :class="node.granted ? 'hover:bg-blue-50/60' : 'bg-gray-50'"
      :style="{ paddingLeft: `${8 + depth * 18}px` }"
      :data-permission-id="node.id"
      :data-granted="node.granted ? 'true' : 'false'"
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

      <svg
        v-if="node.kind === 'menu'"
        class="h-3.5 w-3.5 shrink-0"
        :class="node.granted ? 'text-blue-600' : 'text-gray-300'"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="1.8"
          d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5"
        />
      </svg>
      <span
        v-else
        class="ml-[3px] h-1.5 w-1.5 shrink-0 rounded-full"
        :class="node.granted ? 'bg-indigo-400' : 'bg-gray-300'"
        aria-hidden="true"
      ></span>

      <span
        class="min-w-0 flex-1 truncate py-1.5 text-[11px] leading-tight"
        :class="labelClass"
        :title="node.id"
      >{{ node.label }}</span>

      <span
        v-if="isGrantedPage"
        class="shrink-0 rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-bold text-indigo-500"
        title="可以点进去的子菜单"
      >页面</span>
      <span
        v-else-if="isContainerGroup"
        class="shrink-0 rounded bg-white px-1.5 py-0.5 text-[9px] font-bold text-gray-400 ring-1 ring-gray-200"
        title="本身未授权，仅用于归类其下已授权的权限"
      >仅分组</span>

      <span
        v-if="node.kind === 'menu' && node.grantedElementCount > 0"
        class="shrink-0 text-[10px] text-gray-400"
      >功能点 {{ node.grantedElementCount }}</span>
    </div>

    <!-- 子节点：竖线表达从属关系 -->
    <div
      v-if="hasChildren && !isCollapsed"
      class="border-l border-gray-200"
      :style="{ marginLeft: `${14 + depth * 18}px` }"
    >
      <PersonalPermissionTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :depth="depth + 1"
        :collapsed-ids="collapsedIds"
        @toggle-collapse="forwardToggleCollapse"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { GrantedPermissionNode } from '../../utils/permissionTreeView'

defineOptions({ name: 'PersonalPermissionTreeNode' })

const props = defineProps<{
  node: GrantedPermissionNode
  /** 0 = 一级菜单，每深一层缩进加 18px */
  depth: number
  collapsedIds: string[]
}>()

const emit = defineEmits<{
  (e: 'toggle-collapse', nodeId: string): void
}>()

const hasChildren = computed(() => props.node.children.length > 0)
const isCollapsed = computed(() => props.collapsedIds.includes(props.node.id))
/** 子菜单（能点进去的页面）才标「页面」，一级菜单不标。 */
const isGrantedPage = computed(
  () => props.node.kind === 'menu' && props.depth > 0 && props.node.granted,
)
/** 自身未授权、仅因后代被授权而出现的父级：明确标「仅分组」，不冒充权限。 */
const isContainerGroup = computed(() => !props.node.granted)

const labelClass = computed(() => {
  if (!props.node.granted) return 'text-gray-400'
  return props.node.kind === 'menu' ? 'font-bold text-gray-800' : 'text-gray-600'
})

const forwardToggleCollapse = (nodeId: string) => emit('toggle-collapse', nodeId)
</script>
