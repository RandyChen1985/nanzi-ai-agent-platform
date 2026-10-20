<template>
  <div class="space-y-4">
    <!-- 页头 -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-lg font-bold text-gray-900 dark:text-gray-100">菜单管理</h1>
        <p class="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
          侧栏菜单、权限树与落地页都由这里的数据驱动。内置页面的路由与权限码不可改（会与代码里的权限校验脱钩），
          可改名称、图标、排序与显隐；外链菜单可自由新增与删除。<span class="text-gray-400 dark:text-gray-500">在左侧树上右键可快速新建外链、停用或删除。</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
          :disabled="!canEdit || busy || !tableReady"
          @click="startCreate('group')"
        >新建分组</button>
        <button
          type="button"
          class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
          :disabled="!canEdit || busy || !tableReady"
          @click="startCreate('external')"
        >新建外链菜单</button>
        <button
          type="button"
          class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-700 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
          :disabled="!canEdit || busy || !tableReady"
          @click="showResetConfirm = true"
        >恢复默认</button>
      </div>
    </div>

    <!-- 状态提示 -->
    <div
      v-if="notice.text"
      class="rounded-lg border px-3 py-2 text-xs font-semibold"
      :class="notice.type === 'error'
        ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300'
        : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300'"
    >{{ notice.text }}</div>

    <div
      v-if="!tableReady && !loading"
      class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <p class="font-bold">菜单表尚未建立</p>
      <p class="mt-1">请先由运维执行 <code class="rounded bg-white/60 px-1 py-0.5">db-prod/V176-create_agent_menus.sql</code>（PostgreSQL 为
        <code class="rounded bg-white/60 px-1 py-0.5">db-prod-pg/V77-create_agent_menus.sql</code>）。在此之前侧栏使用内置定义，页面上无法编辑。</p>
    </div>

    <div class="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
      <!-- 左：菜单树 -->
      <section class="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-800">
          <span class="text-xs font-bold text-gray-500 dark:text-gray-400">菜单结构</span>
          <span class="text-[10px] text-gray-400">{{ flatNodes.length }} 个节点</span>
        </div>

        <div v-if="loading" class="space-y-2 p-3">
          <div v-for="index in 8" :key="index" class="h-7 animate-pulse rounded-md bg-gray-100 dark:bg-gray-800"></div>
        </div>

        <div v-else-if="loadError" class="p-4 text-xs text-red-600 dark:text-red-400">
          <p class="font-bold">加载失败</p>
          <p class="mt-1">{{ loadError }}</p>
          <button type="button" class="mt-2 rounded-md bg-red-600 px-2 py-1 font-bold text-white" @click="loadNodes">重试</button>
        </div>

        <div
          v-else
          class="max-h-[calc(100vh-260px)] overflow-y-auto p-2"
          @contextmenu.prevent="openContextMenu($event, null)"
        >
          <div v-for="group in tree" :key="group.code" class="mb-1">
            <!-- 分组行 -->
            <div class="flex items-center gap-1">
              <button
                type="button"
                class="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs font-bold transition-colors"
                :class="selectedCode === group.code
                  ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'
                  : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800'"
                @click="selectNode(group)"
                @contextmenu.prevent.stop="openContextMenu($event, group)"
              >
                <MenuIcon :name="group.icon || 'menu'" class="h-4 w-4 opacity-70" />
                <span class="truncate">{{ group.label }}</span>
                <span v-if="!group.enabled" class="rounded bg-gray-100 px-1 text-[10px] text-gray-500 dark:bg-gray-800">已停用</span>
              </button>
              <button
                type="button"
                class="rounded px-1 text-[10px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                title="上移"
                :disabled="!canEdit"
                @click="moveNode(group, -1)"
              >↑</button>
              <button
                type="button"
                class="rounded px-1 text-[10px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                title="下移"
                :disabled="!canEdit"
                @click="moveNode(group, 1)"
              >↓</button>
            </div>

            <!-- 菜单项行 -->
            <div class="ml-3 mt-0.5 space-y-0.5">
              <div v-for="item in group.children" :key="item.code">
                <div class="flex items-center gap-1">
                  <button
                    type="button"
                    class="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
                    :class="selectedCode === item.code
                      ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'
                      : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800'"
                    @click="selectNode(item)"
                    @contextmenu.prevent.stop="openContextMenu($event, item)"
                  >
                    <MenuIcon :name="item.icon || 'menu'" class="h-3.5 w-3.5 opacity-60" />
                    <span class="truncate">{{ item.label }}</span>
                    <span
                      v-if="item.node_type === 'external'"
                      class="rounded bg-violet-50 px-1 text-[10px] font-bold text-violet-600 dark:bg-violet-500/15 dark:text-violet-300"
                    >{{ item.open_mode === 'embed' ? '内嵌' : '外链' }}</span>
                    <span v-if="!item.enabled" class="rounded bg-gray-100 px-1 text-[10px] text-gray-500 dark:bg-gray-800">已停用</span>
                  </button>
                  <button
                    type="button"
                    class="rounded px-1 text-[10px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                    title="上移"
                    :disabled="!canEdit"
                    @click="moveNode(item, -1)"
                  >↑</button>
                  <button
                    type="button"
                    class="rounded px-1 text-[10px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                    title="下移"
                    :disabled="!canEdit"
                    @click="moveNode(item, 1)"
                  >↓</button>
                </div>

                <!-- 功能点：只读展开 -->
                <div v-if="item.children && item.children.length" class="ml-5 mt-0.5 space-y-0.5 border-l border-dashed border-gray-200 pl-2 dark:border-gray-700">
                  <div
                    v-for="element in item.children"
                    :key="element.code"
                    class="flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px] text-gray-400 dark:text-gray-500"
                    :title="element.code"
                  >
                    <span class="h-1 w-1 flex-shrink-0 rounded-full bg-gray-300 dark:bg-gray-600"></span>
                    <span class="truncate">{{ element.label }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 右：属性面板 -->
      <section class="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div class="border-b border-gray-100 px-4 py-2.5 dark:border-gray-800">
          <span class="text-xs font-bold text-gray-500 dark:text-gray-400">
            {{ isCreating ? '新建' : '属性' }}
          </span>
        </div>

        <div v-if="!draft" class="p-6 text-xs text-gray-400">从左侧选择一个分组或菜单项</div>

        <div v-else class="space-y-4 p-4">
          <div
            v-if="draft.node_type === 'element'"
            class="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[11px] leading-relaxed text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300"
          >
            功能点权限由代码里的 <code>require_permission("element", ...)</code> 决定，后台只能查看，
            不能在菜单管理里增删或改名。
          </div>

          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">名称</span>
              <input
                v-model="draft.label"
                type="text"
                maxlength="64"
                :disabled="readonlyFields.label"
                class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:disabled:bg-gray-800"
              />
            </label>

            <div class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">图标</span>
              <MenuIconPicker
                v-model="draft.icon"
                :disabled="readonlyFields.icon"
                class="mt-1"
              />
            </div>
          </div>

          <!-- 外链专属 -->
          <template v-if="draft.node_type === 'external'">
            <label class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">所属分组</span>
              <select
                v-model="draft.parent_code"
                class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              >
                <option :value="null">（不分组，放在侧栏根级）</option>
                <option v-for="group in groupOptions" :key="group.code" :value="group.code">{{ group.label }}</option>
              </select>
            </label>

            <label class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">外链地址</span>
              <input
                v-model="draft.url"
                type="url"
                placeholder="https://example.com/dashboard"
                :disabled="readonlyFields.url"
                class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
              <span class="mt-1 block text-[10px] text-gray-400">只允许 http:// 与 https://</span>
            </label>

            <div class="grid gap-3 sm:grid-cols-2">
              <label class="block">
                <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">打开方式</span>
                <select
                  v-model="draft.open_mode"
                  :disabled="readonlyFields.open_mode"
                  class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                >
                  <option value="new_tab">新标签页打开</option>
                  <option value="embed">站内内嵌（iframe）</option>
                </select>
              </label>
              <label class="block">
                <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">权限标识（slug）</span>
                <input
                  v-model="draft.slug"
                  type="text"
                  placeholder="grafana"
                  :disabled="readonlyFields.slug"
                  class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
                <span class="mt-1 block text-[10px] text-gray-400">授权码为 menu:ext:&lt;slug&gt;，角色管理里可单独授予</span>
              </label>
            </div>

            <div
              v-if="draft.open_mode === 'embed'"
              class="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
            >
              内嵌依赖目标站点允许被 iframe 引用。若对方设置了 X-Frame-Options 或 CSP frame-ancestors，
              用户看到的会是"无法内嵌"的提示页——这不是平台故障，请改用新标签页打开。
            </div>
          </template>

          <!-- 内置页面：路由只读 -->
          <label v-if="draft.node_type === 'page'" class="block">
            <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">路由地址（只读）</span>
            <input
              :value="draft.path || ''"
              type="text"
              disabled
              class="mt-1 w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-sm text-gray-400 dark:border-gray-700 dark:bg-gray-800"
            />
          </label>

          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">权限码（只读）</span>
              <input
                :value="draft.permission_code || '（公开，所有登录用户可见）'"
                type="text"
                disabled
                class="mt-1 w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-xs text-gray-400 dark:border-gray-700 dark:bg-gray-800"
              />
            </label>
            <label class="block">
              <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">排序值</span>
              <input
                v-model.number="draft.sort_order"
                type="number"
                :disabled="readonlyFields.sort_order"
                class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
            </label>
          </div>

          <label class="block">
            <span class="text-[11px] font-bold text-gray-500 dark:text-gray-400">备注</span>
            <input
              v-model="draft.description"
              type="text"
              maxlength="255"
              :disabled="readonlyFields.description"
              class="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
            />
          </label>

          <div class="flex flex-wrap gap-4 rounded-lg bg-gray-50 px-3 py-2.5 dark:bg-gray-800/60">
            <label class="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <input v-model="draft.enabled" type="checkbox" :disabled="readonlyFields.enabled" />
              启用
            </label>
            <label class="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <input v-model="draft.desktop_only" type="checkbox" :disabled="readonlyFields.desktop_only" />
              仅桌面端显示
            </label>
            <label
              v-if="draft.node_type === 'page'"
              class="inline-flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300"
            >
              <input v-model="draft.is_landing" type="checkbox" :disabled="readonlyFields.is_landing" />
              默认落地页
            </label>
          </div>

          <div class="flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3 dark:border-gray-800">
            <button
              type="button"
              class="rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-blue-700 disabled:opacity-50"
              :disabled="!canEdit || busy || draft.node_type === 'element'"
              @click="save"
            >{{ isCreating ? '创建' : '保存' }}</button>
            <button
              type="button"
              class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              @click="cancelCreate"
            >{{ isCreating ? '取消' : '重置' }}</button>
            <button
              v-if="canDeleteDraft"
              type="button"
              class="ml-auto rounded-lg border border-red-200 px-3 py-1.5 text-xs font-bold text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:hover:bg-red-500/10"
              :disabled="!canEdit || busy || !tableReady"
              @click="showDeleteConfirm = true"
            >{{ draft.node_type === 'group' ? '删除分组' : '删除' }}</button>
            <span v-else-if="draft.builtin" class="ml-auto text-[10px] text-gray-400">内置项不可删除，可停用</span>
            <span v-else-if="draft.node_type === 'group'" class="ml-auto text-[10px] text-gray-400">分组下还有菜单，先移走子项再删</span>
          </div>
        </div>
      </section>
    </div>

    <!-- 右键菜单：树上的快捷操作 -->
    <div
      v-if="contextMenu"
      ref="contextMenuEl"
      class="fixed z-50 w-56 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-2xl dark:border-gray-700 dark:bg-gray-900"
      :style="contextMenuStyle"
      @click.stop
      @contextmenu.prevent
    >
      <p class="truncate border-b border-gray-100 px-3 py-1.5 text-[10px] font-bold text-gray-400 dark:border-gray-800">
        {{ contextMenu.node ? contextMenu.node.label : '菜单结构' }}
      </p>
      <button
        v-for="action in contextActions"
        :key="action.key"
        type="button"
        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors disabled:opacity-40"
        :class="action.danger
          ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10'
          : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800'"
        :disabled="!canEdit || busy || !tableReady"
        @click="runContextAction(action.key)"
      >{{ action.label }}</button>
    </div>

    <!-- 二次确认 -->
    <ConfirmModal
      v-if="showResetConfirm"
      title="恢复默认？"
      message="将把所有内置菜单的名称、图标、排序、启停状态恢复为系统默认值；外链菜单不受影响。此操作不可撤销。"
      confirm-text="确认恢复"
      @confirm="resetBuiltin"
      @cancel="showResetConfirm = false"
    />
    <ConfirmModal
      v-if="showDeleteConfirm"
      :title="deleteTitle"
      :message="deleteMessage"
      confirm-text="确认删除"
      @confirm="deleteSelected"
      @cancel="showDeleteConfirm = false"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import axios from '../utils/axios'
import ConfirmModal from '../components/ConfirmModal.vue'
import MenuIcon from '../components/menu/MenuIcon.vue'
import MenuIconPicker from '../components/menu/MenuIconPicker.vue'
import { refreshNavigation } from '../composables/useNavigation'

/**
 * 菜单管理。
 *
 * 三层结构固定为「分组 → 菜单项（page/external）→ 功能点（element，只读）」，
 * 因此左侧树直接写三层，不用递归组件。
 *
 * 两个刻意的限制：
 * 1. 内置页面的 `path`/`code`/`permission_code` 不可改 —— 它们与 `router/index.ts` 以及
 *    后端 `require_permission` 里的码一一对应，改了就会出现"菜单能点、接口 403"或白屏；
 * 2. 新建只允许分组与外链，不允许凭空造页面 —— 前端没有对应路由，点开必然白屏。
 */
interface AdminMenuNode {
  id: number
  code: string
  parent_code: string | null
  node_type: 'group' | 'page' | 'external' | 'element'
  permission_code: string | null
  label: string
  icon: string | null
  path: string | null
  active_route_names: string[]
  url: string | null
  open_mode: string | null
  sort_order: number
  enabled: boolean
  desktop_only: boolean
  is_landing: boolean
  builtin: boolean
  description: string | null
  children?: AdminMenuNode[]
}

const canEdit = ref(true)
const loading = ref(true)
const busy = ref(false)
const loadError = ref('')
const tableReady = ref(true)
const flatNodes = ref<AdminMenuNode[]>([])
const selectedCode = ref('')
const createMode = ref<'' | 'group' | 'external'>('')
const draft = ref<any>(null)
const showResetConfirm = ref(false)
const showDeleteConfirm = ref(false)
const notice = reactive({ text: '', type: 'success' as 'success' | 'error' })

/** 右键菜单：`node` 为空表示点在树的空白处（只能新建根级节点）。 */
const contextMenu = ref<{ x: number; y: number; node: AdminMenuNode | null } | null>(null)

interface ContextAction {
  key: 'create-external' | 'create-group' | 'set-landing' | 'toggle-enabled' | 'delete'
  label: string
  danger?: boolean
}

const contextActions = computed<ContextAction[]>(() => {
  const node = contextMenu.value?.node
  if (!node) {
    return [
      { key: 'create-group', label: '新建分组' },
      { key: 'create-external', label: '新建外链菜单（根级）' },
    ]
  }
  const actions: ContextAction[] = []
  if (node.node_type === 'group') {
    actions.push({ key: 'create-external', label: `在「${node.label}」下新建外链` })
  } else if (node.node_type === 'page' || node.node_type === 'external') {
    actions.push({ key: 'create-external', label: '在同组下新建外链' })
  }
  if (node.node_type === 'page' && !node.is_landing) {
    actions.push({ key: 'set-landing', label: '设为默认落地页' })
  }
  if (node.node_type !== 'element') {
    actions.push({ key: 'toggle-enabled', label: node.enabled ? '停用' : '启用' })
  }
  const deletable = !node.builtin && (
    node.node_type === 'external' || (node.node_type === 'group' && !hasChildren(node.code))
  )
  if (deletable) {
    actions.push({
      key: 'delete',
      label: node.node_type === 'group' ? '删除空分组' : '删除外链菜单',
      danger: true,
    })
  }
  return actions
})

const contextMenuStyle = computed(() => {
  const menu = contextMenu.value
  if (!menu) return {}
  // 贴着鼠标但不出窗口：菜单宽 224、每项约 30px，标题 28px
  const width = 224
  const height = 28 + contextActions.value.length * 30 + 8
  const left = Math.max(4, Math.min(menu.x, window.innerWidth - width - 8))
  const top = Math.max(4, Math.min(menu.y, window.innerHeight - height - 8))
  return { left: `${left}px`, top: `${top}px` }
})

function openContextMenu(event: MouseEvent, node: AdminMenuNode | null) {
  contextMenu.value = { x: event.clientX, y: event.clientY, node }
}

const contextMenuEl = ref<HTMLElement | null>(null)

function closeContextMenu() {
  contextMenu.value = null
}

/**
 * 点击外部关闭。
 *
 * 必须用捕获阶段 + 排除菜单自身：`mousedown` 早于 `click`，若不加判断，
 * 点菜单项会先清掉 `contextMenu`，随后的 click 就拿不到 node，动作静默失效。
 */
function onDocumentPointerDown(event: MouseEvent) {
  if (!contextMenu.value) return
  if (contextMenuEl.value?.contains(event.target as Node)) return
  closeContextMenu()
}

function onGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeContextMenu()
}

function flash(text: string, type: 'success' | 'error' = 'success') {
  notice.text = text
  notice.type = type
  window.setTimeout(() => {
    if (notice.text === text) notice.text = ''
  }, 3200)
}

const tree = computed<AdminMenuNode[]>(() => {
  const byCode = new Map<string, AdminMenuNode>()
  flatNodes.value.forEach((node) => byCode.set(node.code, { ...node, children: [] }))
  const roots: AdminMenuNode[] = []
  byCode.forEach((node) => {
    const parent = node.parent_code ? byCode.get(node.parent_code) : null
    if (parent) parent.children!.push(node)
    else roots.push(node)
  })
  const sortRec = (items: AdminMenuNode[]) => {
    items.sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code))
    items.forEach((item) => sortRec(item.children || []))
  }
  sortRec(roots)
  return roots
})

const groupOptions = computed(() => flatNodes.value.filter((node) => node.node_type === 'group'))

const canDeleteDraft = computed(() => {
  const node = draft.value
  if (!node || isCreating.value || node.builtin) return false
  if (node.node_type === 'external') return true
  // 自建空分组：误建的分组总得能清掉，但绝不能连带删子树
  return node.node_type === 'group' && !hasChildren(node.code)
})

const deleteTitle = computed(() => (draft.value?.node_type === 'group' ? '删除分组？' : '删除外链菜单？'))

const deleteMessage = computed(() => (draft.value?.node_type === 'group'
  ? '这是一个自建的空分组，删除后不可恢复；内置分组不可删除。'
  : '删除后该菜单与其授权记录会被一并清理，已授予的角色将失去这一入口。内置菜单不可删除。'))

/** 分组下是否还有子项：决定"删除空分组"是否可用。 */
function hasChildren(code: string): boolean {
  return flatNodes.value.some((item) => item.parent_code === code)
}

const isCreating = computed(() => Boolean(createMode.value))

const readonlyFields = computed((): Record<string, boolean> => {
  const node = draft.value
  if (!node) return {}
  if (node.node_type === 'element') {
    return { label: true, icon: true, enabled: true, desktop_only: true, sort_order: true, description: true }
  }
  if (node.builtin) {
    // 内置项：允许改展示属性，禁止改身份字段
    return { url: true, open_mode: true, slug: true }
  }
  return {}
})

async function loadNodes() {
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get('/api/portal/menus')
    flatNodes.value = response.data?.nodes || []
    tableReady.value = Boolean(response.data?.table_ready)
    const first = flatNodes.value[0]
    if (!selectedCode.value && first) {
      selectNode(first)
    } else if (selectedCode.value) {
      const still = flatNodes.value.find((node) => node.code === selectedCode.value)
      if (still) selectNode(still)
    }
  } catch (error: any) {
    loadError.value = error?.response?.data?.detail || error?.message || '请求失败'
    if (error?.response?.status === 403) canEdit.value = false
  } finally {
    loading.value = false
  }
}

function selectNode(node: AdminMenuNode) {
  createMode.value = ''
  selectedCode.value = node.code
  draft.value = {
    ...node,
    slug: node.node_type === 'external' ? (node.permission_code || '').replace('menu:ext:', '') : '',
  }
}

/**
 * 新建外链时的默认归属分组。
 *
 * 取「当前选中的节点」所在的组，而不是固定第一个组：在某个分组下点新建、
 * 或者在它的子项上点新建，绝大多数情况就是想加到那里，省一次手动选择。
 */
function defaultParentCode(): string | null {
  const current = flatNodes.value.find((item) => item.code === selectedCode.value)
  if (current) {
    if (current.node_type === 'group') return current.code
    if (current.parent_code) return current.parent_code
  }
  // 树的第一个节点是「概览」（页面，无父级），挂上去后端会拒绝"父级必须是分组"
  return groupOptions.value[0]?.code || null
}

function startCreate(kind: 'group' | 'external', parentCode?: string | null) {
  // 先解析默认分组：defaultParentCode() 依赖 selectedCode，清空之后就取不到了
  const resolvedParent = kind === 'external'
    ? (parentCode === undefined ? defaultParentCode() : parentCode)
    : null
  createMode.value = kind
  selectedCode.value = ''
  draft.value = {
    node_type: kind,
    label: '',
    icon: kind === 'group' ? 'menu' : 'chat_bubble_left_right',
    parent_code: resolvedParent,
    url: '',
    open_mode: 'new_tab',
    slug: '',
    sort_order: 999,
    enabled: true,
    desktop_only: false,
    is_landing: false,
    permission_code: null,
    path: null,
    description: '',
    builtin: false,
  }
}

function cancelCreate() {
  if (createMode.value) {
    createMode.value = ''
    draft.value = null
    const first = flatNodes.value[0]
    if (first) selectNode(first)
    return
  }
  const node = flatNodes.value.find((item) => item.code === selectedCode.value)
  if (node) selectNode(node)
}

function payloadFromDraft() {
  const node = draft.value
  const payload: Record<string, any> = {
    label: node.label,
    icon: node.icon || null,
    enabled: Boolean(node.enabled),
    desktop_only: Boolean(node.desktop_only),
    sort_order: Number(node.sort_order) || 0,
    description: node.description || null,
  }
  if (node.node_type === 'page') payload.is_landing = Boolean(node.is_landing)
  if (node.node_type === 'external') {
    payload.url = node.url
    payload.open_mode = node.open_mode
    payload.slug = (node.slug || '').replace('menu:ext:', '')
    // 新建与编辑都要带上分组：留空表示放在侧栏根级
    payload.parent_code = node.parent_code || null
  }
  return payload
}

async function save() {
  if (!draft.value) return
  busy.value = true
  try {
    if (createMode.value) {
      const payload: Record<string, any> = {
        ...payloadFromDraft(),
        node_type: createMode.value,
      }
      const response = await axios.post('/api/portal/menus', payload)
      flash('已创建')
      await loadNodes()
      const created = flatNodes.value.find((node) => node.code === response.data?.code)
      if (created) selectNode(created)
    } else {
      await axios.put(`/api/portal/menus/${draft.value.id}`, payloadFromDraft())
      flash('已保存')
      await loadNodes()
    }
    await refreshNavigation()
  } catch (error: any) {
    flash(error?.response?.data?.detail || '保存失败', 'error')
  } finally {
    busy.value = false
  }
}

async function deleteSelected() {
  showDeleteConfirm.value = false
  if (!draft.value) return
  busy.value = true
  try {
    await axios.delete(`/api/portal/menus/${draft.value.id}`)
    flash('已删除')
    draft.value = null
    selectedCode.value = ''
    await loadNodes()
    await refreshNavigation()
  } catch (error: any) {
    flash(error?.response?.data?.detail || '删除失败', 'error')
  } finally {
    busy.value = false
  }
}

async function resetBuiltin() {
  showResetConfirm.value = false
  busy.value = true
  try {
    const response = await axios.post('/api/portal/menus/reset')
    flash(`已恢复 ${response.data?.restored ?? 0} 个内置项`)
    await loadNodes()
    await refreshNavigation()
  } catch (error: any) {
    flash(error?.response?.data?.detail || '恢复失败', 'error')
  } finally {
    busy.value = false
  }
}

async function runContextAction(key: ContextAction['key']) {
  const node = contextMenu.value?.node
  closeContextMenu()
  if (key === 'create-group') {
    startCreate('group')
    return
  }
  if (key === 'create-external') {
    const parent = !node ? null : node.node_type === 'group' ? node.code : node.parent_code
    startCreate('external', parent ?? defaultParentCode())
    return
  }
  if (!node) return
  if (key === 'delete') {
    selectNode(node)
    showDeleteConfirm.value = true
    return
  }
  if (key === 'set-landing') {
    await patchNode(node, { is_landing: true }, '已设为默认落地页')
    return
  }
  if (key === 'toggle-enabled') {
    await patchNode(node, { enabled: !node.enabled }, node.enabled ? '已停用' : '已启用')
  }
}

/** 树上的单字段快捷修改（停用/启用、设为落地页）：与属性面板保存走同一接口。 */
async function patchNode(node: AdminMenuNode, patch: Record<string, any>, successText: string) {
  if (!canEdit.value || busy.value) return
  busy.value = true
  try {
    await axios.put(`/api/portal/menus/${node.id}`, patch)
    flash(successText)
    await loadNodes()
    await refreshNavigation()
  } catch (error: any) {
    flash(error?.response?.data?.detail || '操作失败', 'error')
  } finally {
    busy.value = false
  }
}

/** 上移/下移：与相邻兄弟交换 sort_order，再批量提交 */
async function moveNode(node: AdminMenuNode, direction: -1 | 1) {
  if (!canEdit.value || busy.value) return
  const siblings = flatNodes.value
    .filter((item) => (item.parent_code || null) === (node.parent_code || null))
    .sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code))
  const index = siblings.findIndex((item) => item.code === node.code)
  const targetIndex = index + direction
  if (index < 0 || targetIndex < 0 || targetIndex >= siblings.length) return
  const swapped = [...siblings]
  const moved = swapped.splice(index, 1)[0]
  if (!moved) return
  swapped.splice(targetIndex, 0, moved)
  const items = swapped.map((item, position) => ({
    code: item.code,
    parent_code: item.parent_code,
    sort_order: (position + 1) * 10,
  }))
  busy.value = true
  try {
    await axios.post('/api/portal/menus/reorder', { items })
    await loadNodes()
    await refreshNavigation()
  } catch (error: any) {
    flash(error?.response?.data?.detail || '排序失败', 'error')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadNodes()
  // 右键菜单：点击任意位置、滚动或改变窗口大小都收起
  document.addEventListener('mousedown', onDocumentPointerDown, true)
  window.addEventListener('scroll', closeContextMenu, true)
  window.addEventListener('resize', closeContextMenu)
  document.addEventListener('keydown', onGlobalKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown, true)
  window.removeEventListener('scroll', closeContextMenu, true)
  window.removeEventListener('resize', closeContextMenu)
  document.removeEventListener('keydown', onGlobalKeydown)
})
</script>
