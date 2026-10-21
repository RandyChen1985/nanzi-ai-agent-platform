<template>
  <div class="space-y-4">
    <!-- 页头 -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-bold text-gray-900 sm:text-2xl dark:text-gray-100">部门管理</h1>
        <p class="mt-1 text-sm leading-relaxed text-gray-500 dark:text-gray-400">
          组织架构由这里维护。一个用户可以同时属于多个部门，「主部门」决定其部门代码与数据权限口径。
          <span class="text-gray-400 dark:text-gray-500">部门代码唯一，改编码会同步刷新成员快照。</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <template v-if="canEdit">
          <button
            type="button"
            class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
            :disabled="loading"
            @click="openCreate(null)"
          >新建部门</button>
        </template>
        <span
          v-else
          class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-[11px] font-bold text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
          title="部门维护需要 element:dept:edit 权限，可在「角色管理」里授予"
        >只读模式 · 无部门编辑权限</span>
      </div>
    </div>

    <div class="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
      <!-- 左：部门树 -->
      <div class="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2 dark:border-gray-800">
          <span class="text-xs font-bold text-gray-500 dark:text-gray-400">组织架构</span>
          <span class="flex items-center gap-1 text-[11px] text-gray-400">
            <span>
              {{ nodeCount }} 个部门<span v-if="memberTotal"> · {{ memberTotal }} 人</span>
            </span>
            <InfoTooltip v-if="memberTotal" align="right" :text="memberTotalHint" />
          </span>
        </div>
        <div class="max-h-[calc(100vh-260px)] overflow-y-auto p-2">
          <div v-if="loading" class="px-3 py-6 text-center text-xs text-gray-400">加载中…</div>
          <div v-else-if="loadError" class="px-3 py-6 text-center text-xs text-red-500">
            {{ loadError }}
            <button type="button" class="ml-1 underline" @click="fetchTree">重试</button>
          </div>
          <DepartmentTree
            v-else
            :nodes="tree"
            :selected-id="selectedId"
            :expanded-ids="expandedIds"
            :readonly="!canEdit"
            :can-delete="canDelete"
            @select="selectNode"
            @toggle-expand="toggleExpand"
            @action="handleTreeAction"
          />
        </div>
      </div>

      <!-- 右：信息 / 成员 -->
      <div class="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div class="flex items-center gap-1 border-b border-gray-100 px-3 py-2 dark:border-gray-800">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm font-bold transition-colors"
            :class="
              activeTab === tab.id
                ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300'
                : 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800'
            "
            @click="activeTab = tab.id"
          >{{ tab.label }}</button>
          <span v-if="!selectedNode" class="ml-auto text-xs text-gray-400">未选择部门</span>
        </div>

        <div v-if="!selectedNode" class="p-8 text-center text-sm text-gray-400">
          从左侧选择一个部门，或点「新建部门」创建
        </div>

        <template v-else>
          <DepartmentInfoPanel
            v-if="activeTab === 'info'"
            :dept="selectedNode"
            :parent-label="parentLabelOf(selectedNode)"
            :readonly="!canEdit"
            :saving="saving"
            :can-delete="canDelete"
            @save="saveInfo"
            @remove="requestDelete(selectedNode)"
          />
          <DepartmentMembersPanel
            v-else
            :dept-id="selectedNode.id"
            :has-children="(selectedNode.children?.length || 0) > 0"
            :can-edit="canEdit"
            :can-edit-user="canEditUser"
            :can-jump-to-users="canJumpToUsers"
            @changed="fetchTree"
          />
        </template>
      </div>
    </div>

    <!-- 新建部门 -->
    <Teleport to="body">
      <div
        v-if="createDialog.show"
        class="fixed inset-0 z-[12000] flex items-center justify-center bg-black/40 p-4"
        @click.self="closeCreate"
      >
        <form
          class="w-[min(420px,100vw)] space-y-3 rounded-2xl bg-white p-4 shadow-xl dark:bg-gray-900"
          @submit.prevent="submitCreate"
        >
          <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100">新建部门</h3>
          <label class="block">
            <span class="mb-1 block text-[11px] font-bold text-gray-500 dark:text-gray-400">上级部门</span>
            <input
              :value="createDialog.parentLabel"
              type="text"
              readonly
              class="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400"
            />
          </label>
          <label class="block">
            <span class="mb-1 block text-[11px] font-bold text-gray-500 dark:text-gray-400">部门名称</span>
            <input
              v-model="createDialog.name"
              type="text"
              required
              placeholder="例如：上海分公司"
              class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            />
          </label>
          <label class="block">
            <span class="mb-1 block text-[11px] font-bold text-gray-500 dark:text-gray-400">部门编码</span>
            <input
              v-model="createDialog.code"
              type="text"
              required
              placeholder="SH"
              :class="[
                'w-full rounded-lg border px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 dark:bg-gray-800 dark:text-gray-100',
                createCodeAvailable === false
                  ? 'border-red-400 focus:ring-red-500 dark:border-red-500/60'
                  : 'border-gray-300 focus:ring-blue-500 dark:border-gray-700'
              ]"
            />
            <span v-if="createCodeChecking" class="mt-1 block text-[11px] text-gray-400">正在检查编码…</span>
            <span v-else-if="createCodeAvailable === false" class="mt-1 block text-[11px] font-bold text-red-500">
              {{ createCodeMessage }}
            </span>
            <span v-else-if="createCodeAvailable === true" class="mt-1 block text-[11px] font-bold text-emerald-600">
              编码可用
            </span>
            <span v-else class="mt-1 block text-[11px] text-gray-400">
              全局唯一，建议用大写字母与下划线。不能是纯数字（会和内部 id 混淆）。
            </span>
          </label>
          <div class="flex justify-end gap-2">
            <button
              type="button"
              class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 dark:border-gray-700 dark:text-gray-300"
              @click="closeCreate"
            >取消</button>
            <button
              type="submit"
              class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700 disabled:opacity-50"
              :disabled="creating"
            >{{ creating ? '创建中…' : '创建' }}</button>
          </div>
        </form>
      </div>
    </Teleport>

    <ConfirmModal
      v-if="deleteTarget"
      title="删除部门"
      :message="deleteMessage"
      confirm-text="删除"
      type="danger"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import axios from '../utils/axios'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import { useDepartmentCodeAvailability } from '../composables/useDepartmentCodeAvailability'
import { extractApiErrorDetail } from '../utils/apiErrorDetail'
import ConfirmModal from '../components/ConfirmModal.vue'
import DepartmentTree, { type DeptNode } from '../components/department/DepartmentTree.vue'
import DepartmentInfoPanel from '../components/department/DepartmentInfoPanel.vue'
import InfoTooltip from '../components/InfoTooltip.vue'
import DepartmentMembersPanel from '../components/department/DepartmentMembersPanel.vue'

/**
 * 部门管理页：左树 + 右侧 tabs（部门信息 / 成员）。
 *
 * 布局照搬菜单管理（树 + 表单），但成员单独一个 tab —— 部门维护的是组织结构，
 * 成员归属是附着在结构上的另一件事，混在一个面板里会让两者都局促。
 */
const { showToast } = useToast()
const { hasPermission } = useUser()

const tree = ref<DeptNode[]>([])
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const creating = ref(false)

const selectedId = ref<number | null>(null)
const expandedIds = ref<number[]>([])
const activeTab = ref<'info' | 'members'>('members')

// 日常用途是"看这个部门有哪些人"，所以成员在前、并作为默认页
const tabs = [
  { id: 'members' as const, label: '成员' },
  { id: 'info' as const, label: '部门信息' }
]

// 后端 403 时降级为只读（本地权限快照可能过期）
const editDenied = ref(false)
const canEdit = computed(() => hasPermission('element:dept:edit') && !editDenied.value)
const canDelete = computed(() => hasPermission('element:dept:delete') && !editDenied.value)
const canEditUser = computed(() => hasPermission('element:user:edit'))
const canJumpToUsers = computed(() => hasPermission('menu:system:users'))

const {
  checking: createCodeChecking,
  available: createCodeAvailable,
  message: createCodeMessage,
  checkDebounced: checkCreateCode,
  ensureAvailable: ensureCreateCodeAvailable,
  reset: resetCreateCodeCheck,
} = useDepartmentCodeAvailability()

const createDialog = ref({
  show: false,
  parentId: null as number | null,
  parentLabel: '无（根部门）',
  name: '',
  code: ''
})

watch(
  () => createDialog.value.code,
  (next) => {
    if (!createDialog.value.show) return
    checkCreateCode(next)
  }
)

const deleteTarget = ref<DeptNode | null>(null)

const nodeCount = computed(() => countNodes(tree.value))
/** 去重人数：各节点直属人数相加是"人次"，多部门用户会重复计数，所以用后端的值 */
const memberTotal = ref<number>(0)

// 树上徽标相加比这个数字大是正常的，必须讲清楚，否则会被当成算错
const memberTotalHint =
  '人数已按用户去重：一个人同时属于多个部门只计 1 次，所以会少于各部门徽标相加的结果（徽标是各部门的直属人数）。'

function countNodes(nodes: DeptNode[]): number {
  return nodes.reduce((sum, node) => sum + 1 + countNodes(node.children || []), 0)
}

function findNode(nodes: DeptNode[], id: number): DeptNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    const hit = findNode(node.children || [], id)
    if (hit) return hit
  }
  return null
}

function findSiblings(nodes: DeptNode[], node: DeptNode): DeptNode[] {
  if (node.parent_id === null || node.parent_id === undefined) return nodes
  const parent = findNode(nodes, node.parent_id)
  return parent?.children || []
}

const selectedNode = computed(() =>
  selectedId.value === null ? null : findNode(tree.value, selectedId.value)
)

const parentLabelOf = (node: DeptNode | null) => {
  if (!node || node.parent_id === null) return '无（根部门）'
  const parent = findNode(tree.value, node.parent_id)
  return parent ? `${parent.name}（${parent.code}）` : '未知'
}

const deleteMessage = computed(() => {
  const node = deleteTarget.value
  if (!node) return ''
  return `将删除部门「${node.name}」。\n\n只有没有任何子部门、也没有成员的部门才能删除；若有残留，系统会拒绝并提示。`
})

/** 默认选中第一个根部门，并展开它的一级子节点（能看到分支但不至于全展开）。 */
const autoSelectFirstRoot = () => {
  const root = tree.value[0]
  if (!root) return
  selectedId.value = root.id
  expandedIds.value = [...new Set([...expandedIds.value, root.id])]
}

const fetchTree = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get('/api/portal/departments/tree', {
      params: { include_disabled: true }
    })
    tree.value = response.data.items || []
    memberTotal.value = Number(response.data.member_total || 0)
    if (selectedId.value !== null && !findNode(tree.value, selectedId.value)) {
      selectedId.value = null
    }
    // 刚进来不该让人对着空态猜下一步：自动落到第一个根部门
    if (selectedId.value === null && tree.value.length) {
      autoSelectFirstRoot()
    }
  } catch (e: any) {
    if (e?.response?.status === 403) editDenied.value = true
    loadError.value = extractApiErrorDetail(e?.response?.data, '部门树加载失败')
  } finally {
    loading.value = false
  }
}

const selectNode = (node: DeptNode) => {
  selectedId.value = node.id
}

const toggleExpand = (id: number) => {
  if (expandedIds.value.includes(id)) {
    expandedIds.value = expandedIds.value.filter((item) => item !== id)
  } else {
    expandedIds.value = [...expandedIds.value, id]
  }
}

const openCreate = (parent: DeptNode | null) => {
  resetCreateCodeCheck()
  createDialog.value = {
    show: true,
    parentId: parent ? parent.id : null,
    parentLabel: parent ? `${parent.name}（${parent.code}）` : '无（根部门）',
    name: '',
    code: ''
  }
}

const closeCreate = () => {
  createDialog.value.show = false
  resetCreateCodeCheck()
}

const submitCreate = async () => {
  const { name, code, parentId } = createDialog.value
  if (!name.trim() || !code.trim()) {
    showToast('部门名称与编码都要填', 'warning')
    return
  }
  // 提交前兜底：输入期可能还没查完，或被并发创建抢先占用
  const codeCheckResult = await ensureCreateCodeAvailable(code)
  if (!codeCheckResult.ok) {
    showToast(codeCheckResult.message || '该编码不可用', 'error')
    return
  }
  creating.value = true
  try {
    const response = await axios.post('/api/portal/departments', {
      name: name.trim(),
      code: code.trim(),
      parent_id: parentId
    })
    showToast('部门已创建', 'success')
    closeCreate()
    await fetchTree()
    const newId = Number(response.data?.id)
    if (newId) {
      selectedId.value = newId
      if (parentId) expandedIds.value = [...new Set([...expandedIds.value, parentId])]
      activeTab.value = 'info'
    }
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '创建失败'), 'error')
  } finally {
    creating.value = false
  }
}

const saveInfo = async (payload: {
  name: string
  code: string
  description: string
  sort_order: number
  status: number
}) => {
  if (!selectedNode.value) return
  saving.value = true
  try {
    await axios.put(`/api/portal/departments/${selectedNode.value.id}`, {
      name: payload.name.trim(),
      code: payload.code.trim(),
      description: payload.description,
      sort_order: payload.sort_order,
      status: payload.status
    })
    showToast('已保存', 'success')
    await fetchTree()
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '保存失败'), 'error')
  } finally {
    saving.value = false
  }
}

const handleTreeAction = async (payload: { type: string; node: DeptNode }) => {
  const { type, node } = payload
  if (type === 'create-child') {
    expandedIds.value = [...new Set([...expandedIds.value, node.id])]
    openCreate(node)
    return
  }
  if (type === 'delete') {
    requestDelete(node)
    return
  }
  if (type === 'move-up' || type === 'move-down') {
    await moveNode(node, type === 'move-up' ? -1 : 1)
  }
}

const moveNode = async (node: DeptNode, delta: number) => {
  const siblings = findSiblings(tree.value, node)
  const index = siblings.findIndex((item) => item.id === node.id)
  const target = index + delta
  if (index < 0 || target < 0 || target >= siblings.length) return

  const ordered: number[] = siblings.map((item) => item.id)
  const current = ordered[index]
  const swapped = ordered[target]
  if (current === undefined || swapped === undefined) return
  ordered[index] = swapped
  ordered[target] = current

  try {
    await axios.post('/api/portal/departments/reorder', {
      parent_id: node.parent_id ?? null,
      ordered_ids: ordered
    })
    await fetchTree()
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '排序失败'), 'error')
  }
}

const requestDelete = (node: DeptNode) => {
  deleteTarget.value = node
}

const confirmDelete = async () => {
  const node = deleteTarget.value
  deleteTarget.value = null
  if (!node) return
  try {
    await axios.delete(`/api/portal/departments/${node.id}`)
    showToast('部门已删除', 'success')
    if (selectedId.value === node.id) selectedId.value = null
    await fetchTree()
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '删除失败'), 'error')
  }
}

onMounted(fetchTree)
</script>
