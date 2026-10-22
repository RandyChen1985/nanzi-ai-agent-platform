<template>
  <div class="space-y-3 p-4">
    <!-- 工具栏 -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative min-w-[180px] flex-1">
        <input
          v-model="search"
          type="search"
          placeholder="搜索姓名 / 账号 / 邮箱"
          class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
        />
      </div>
      <label
        v-if="hasChildren"
        class="flex shrink-0 cursor-pointer select-none items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-2 text-xs text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
        title="勾选后显示本部门及所有子部门的成员（同一人只出现一次）"
      >
        <input v-model="includeSub" type="checkbox" class="h-3.5 w-3.5 rounded border-gray-300" />
        含子部门
      </label>
      <button
        v-if="canEdit && deptId"
        type="button"
        class="rounded-lg bg-blue-600 px-3.5 py-2 text-sm font-bold text-white hover:bg-blue-700"
        @click="openAddDialog"
      >+ 添加成员</button>
    </div>

    <!-- 批量操作条 -->
    <div
      v-if="canEdit && selectedIds.length"
      class="flex flex-wrap items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300"
    >
      <span class="font-bold">已选 {{ selectedIds.length }} 人</span>
      <button
        type="button"
        class="rounded-lg border border-red-200 bg-white px-2 py-1 font-bold text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:bg-gray-900"
        :disabled="acting !== null"
        @click="requestRemove(selectedIds)"
      >移出本部门</button>
      <button
        type="button"
        class="rounded px-2 py-1 text-blue-600 hover:underline dark:text-blue-300"
        @click="selectedIds = []"
      >取消选择</button>
    </div>

    <!-- 表格 -->
    <div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
      <table class="min-w-full">
        <thead class="bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
          <tr>
            <th v-if="canEdit" class="w-10 px-4 py-3">
              <input
                type="checkbox"
                :checked="allSelected"
                :disabled="!members.length"
                class="h-3.5 w-3.5 rounded border-gray-300"
                @change="toggleSelectAll"
              />
            </th>
            <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider">成员</th>
            <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider">状态</th>
            <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider">还属于</th>
            <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider">上次登录</th>
            <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
          <tr v-if="loading">
            <td :colspan="canEdit ? 6 : 5" class="px-4 py-10 text-center text-sm text-gray-400">加载中…</td>
          </tr>
          <tr v-else-if="loadError">
            <td :colspan="canEdit ? 6 : 5" class="px-4 py-10 text-center text-sm text-red-500">
              {{ loadError }}
              <button type="button" class="ml-2 underline" @click="fetchMembers">重试</button>
            </td>
          </tr>
          <tr v-else-if="!members.length">
            <td :colspan="canEdit ? 6 : 5" class="px-4 py-10 text-center text-sm text-gray-400">
              该部门还没有成员
            </td>
          </tr>
          <template v-else>
          <tr
            v-for="member in members"
            :key="member.id"
            class="hover:bg-gray-50 dark:hover:bg-gray-800/60"
          >
            <td v-if="canEdit" class="px-4 py-3">
              <input
                v-model="selectedIds"
                type="checkbox"
                :value="member.id"
                class="h-3.5 w-3.5 rounded border-gray-300"
              />
            </td>
            <td class="px-4 py-3 text-sm">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-sm font-bold text-gray-800 dark:text-gray-100">{{ member.user_name }}</span>
                <span v-if="member.real_name" class="text-gray-500 dark:text-gray-400">
                  {{ member.real_name }}
                </span>
                <span
                  v-if="member.is_primary"
                  class="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-bold text-blue-600 dark:bg-blue-500/10 dark:text-blue-300"
                  :title="member.primary_dept_name ? `主部门：${member.primary_dept_name}` : '主部门'"
                >主部门</span>
                <span
                  v-if="member.role === 'admin'"
                  class="rounded bg-purple-50 px-1.5 py-0.5 text-[11px] font-bold text-purple-600 dark:bg-purple-500/10 dark:text-purple-300"
                >管理员</span>
              </div>
              <div v-if="member.email" class="mt-0.5 text-[11px] text-gray-400">{{ canEditUser ? member.email : maskEmail(member.email) }}</div>
            </td>
            <td class="px-4 py-3 text-sm">
              <span
                class="rounded px-1.5 py-0.5 text-[11px] font-bold"
                :class="
                  member.status === 1
                    ? 'bg-green-50 text-green-600 dark:bg-green-500/10 dark:text-green-300'
                    : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300'
                "
              >{{ member.status === 1 ? '启用' : member.status === 2 ? '待审核' : '禁用' }}</span>
            </td>
            <td class="px-4 py-3 text-sm">
              <span
                v-if="otherDepartments(member).length"
                class="text-xs text-gray-500 dark:text-gray-400"
                :title="otherDepartments(member).join('、')"
              >{{ otherDepartments(member).slice(0, 2).join('、') }}<template v-if="otherDepartments(member).length > 2"> +{{ otherDepartments(member).length - 2 }}</template></span>
              <span v-else class="text-[11px] text-gray-300 dark:text-gray-600">—</span>
            </td>
            <td class="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
              {{ member.last_login_at ? formatTime(member.last_login_at) : '从未登录' }}
            </td>
            <td class="px-3 py-2 text-right">
              <div class="inline-flex flex-wrap items-center justify-end gap-1">
                <!-- 「设为主部门」「移出」只对**直属本部门**的成员成立：接口按当前部门 id
                     校验归属，对子树里那些仅属于子部门的人调用会被后端拒绝，或者移出 0 行
                     却因为「含子部门」还留在列表里。所以这两个操作在这里就不给出来。 -->
                <button
                  v-if="canEdit && member.is_direct && !member.is_primary"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] font-bold text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-50 dark:hover:bg-gray-700 dark:hover:text-gray-100"
                  :disabled="acting !== null"
                  title="把本部门设为该成员的主部门"
                  @click="setPrimary(member)"
                >设为主部门</button>
                <button
                  v-if="canEdit && member.is_direct"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] font-bold text-red-500 hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-500/10"
                  :disabled="acting !== null"
                  @click="requestRemove([member.id])"
                >移出</button>
                <button
                  v-if="canEditUser"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] font-bold text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-50 dark:hover:bg-gray-700 dark:hover:text-gray-100"
                  :disabled="acting !== null"
                  @click="toggleStatus(member)"
                >{{ member.status === 1 ? '禁用' : '启用' }}</button>
                <button
                  v-if="canEditUser"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] font-bold text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:hover:bg-gray-700 dark:hover:text-gray-100"
                  title="到用户管理编辑该用户"
                  @click="openUserEditor(member)"
                >编辑</button>
                <button
                  v-if="canEditUser"
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] font-bold text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:hover:bg-gray-700 dark:hover:text-gray-100"
                  @click="openPasswordDialog(member)"
                >密码</button>
                <button
                  v-if="canJumpToUsers"
                  type="button"
                  class="rounded px-1 py-0.5 text-[11px] font-bold text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-500/10"
                  title="到用户管理里查看该用户"
                  @click="jumpToUser(member)"
                >↗</button>
              </div>
            </td>
          </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- 分页 -->
    <div v-if="total > pageSize" class="flex items-center justify-between text-[11px] text-gray-500">
      <span>共 {{ total }} 人 · 第 {{ page }} / {{ totalPages }} 页</span>
      <div class="flex gap-1">
        <button
          type="button"
          class="rounded border border-gray-200 px-2 py-1 disabled:opacity-40 dark:border-gray-700"
          :disabled="page <= 1 || loading"
          @click="goPage(page - 1)"
        >上一页</button>
        <button
          type="button"
          class="rounded border border-gray-200 px-2 py-1 disabled:opacity-40 dark:border-gray-700"
          :disabled="page >= totalPages || loading"
          @click="goPage(page + 1)"
        >下一页</button>
      </div>
    </div>

    <!-- 添加成员 -->
    <Teleport to="body">
      <div
        v-if="showAddDialog"
        class="fixed inset-0 z-[12000] flex items-center justify-center bg-black/40 p-4"
        @click.self="closeAddDialog"
      >
        <div
          class="flex max-h-[82vh] w-[min(760px,100vw)] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-gray-900"
        >
          <!-- 头部 -->
          <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
            <div class="min-w-0">
              <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100">添加成员</h3>
              <p class="mt-1 text-[11px] leading-relaxed text-gray-400">
                勾选要加入本部门的用户。候选列表已排除本部门现有成员；一个用户可以同时属于多个部门。
              </p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800"
              @click="closeAddDialog"
            >✕</button>
          </div>

          <!-- 搜索 + 批量选择 -->
          <div class="flex flex-wrap items-center gap-2 px-5 py-3">
            <div class="relative min-w-[180px] flex-1">
              <svg
                class="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
              </svg>
              <input
                v-model="candidateSearch"
                type="search"
                placeholder="搜索姓名 / 账号 / 邮箱"
                class="w-full rounded-lg border border-gray-300 py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
            <label
              class="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-2 text-[11px] font-bold text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              title="只显示尚未加入任何部门的用户"
            >
              <input
                v-model="unassignedOnly"
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-gray-300"
                @change="onUnassignedOnlyChange"
              />
              仅未分配部门
            </label>
            <button
              v-if="candidates.length"
              type="button"
              class="shrink-0 rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] font-bold text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              @click="toggleCandidateAll"
            >{{ allCandidatesSelected ? '取消全选' : '全选本页' }}</button>
          </div>

          <!-- 候选列表 -->
          <div class="min-h-[240px] flex-1 overflow-y-auto px-3 pb-2">
            <div v-if="candidateLoading" class="flex flex-col items-center gap-2 py-12 text-xs text-gray-400">
              <span class="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500"></span>
              正在加载候选用户…
            </div>
            <div v-else-if="!candidates.length" class="flex flex-col items-center gap-1.5 py-12 text-center">
              <svg class="h-8 w-8 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="1.5"
                  d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-1.13a4 4 0 10-4-4 4 4 0 004 4z"
                />
              </svg>
              <p class="text-sm font-bold text-gray-500 dark:text-gray-400">
                {{ unassignedOnly ? '没有未分配部门的用户' : '没有可添加的用户' }}
              </p>
              <p v-if="unassignedOnly" class="text-[11px] text-gray-400">
                候选里的人都已属于某个部门，取消勾选可查看全部
              </p>
              <p v-else class="text-[11px] text-gray-400">换个关键词试试，或到用户管理里先创建账号</p>
            </div>
            <template v-else>
              <label
                v-for="user in candidates"
                :key="user.id"
                class="group flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2 transition-colors"
                :class="
                  candidateSelected.includes(user.id)
                    ? 'border-blue-300 bg-blue-50/70 dark:border-blue-500/40 dark:bg-blue-500/10'
                    : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-800'
                "
              >
                <input
                  v-model="candidateSelected"
                  type="checkbox"
                  :value="user.id"
                  class="h-4 w-4 shrink-0 rounded border-gray-300"
                />
                <span
                  class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
                  :class="avatarClass(user)"
                >{{ initialOf(user) }}</span>
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-1.5">
                    <span class="truncate text-sm font-bold text-gray-800 dark:text-gray-100">{{ user.user_name }}</span>
                    <span v-if="user.real_name" class="truncate text-xs text-gray-500 dark:text-gray-400">
                      {{ user.real_name }}
                    </span>
                    <span
                      v-if="user.role === 'admin'"
                      class="shrink-0 rounded bg-violet-50 px-1 text-[11px] font-bold text-violet-600 dark:bg-violet-500/10 dark:text-violet-300"
                    >管理员</span>
                    <span
                      v-if="Number(user.status) === 0"
                      class="shrink-0 rounded bg-amber-50 px-1 text-[11px] font-bold text-amber-600 dark:bg-amber-500/10 dark:text-amber-300"
                    >禁用</span>
                  </span>
                  <span class="mt-0.5 flex items-center gap-2 text-[11px] text-gray-400">
                    <span v-if="user.email" class="truncate">{{ canEditUser ? user.email : maskEmail(user.email) }}</span>
                    <span v-if="deptSummary(user)" class="shrink-0 rounded bg-gray-100 px-1 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                      已在 {{ deptSummary(user) }}
                    </span>
                  </span>
                </span>
              </label>
            </template>
          </div>

          <!-- 底部 -->
          <div
            class="flex items-center justify-between gap-2 border-t border-gray-100 px-5 py-3 dark:border-gray-800"
          >
            <div class="flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
              <span>已选 <b class="text-blue-600 dark:text-blue-400">{{ candidateSelected.length }}</b> 人</span>
              <button
                v-if="candidateSelected.length"
                type="button"
                class="text-blue-600 hover:underline dark:text-blue-400"
                @click="candidateSelected = []"
              >清空</button>
              <span v-if="candidates.length >= 50" class="text-gray-400">· 最多显示 50 位，可搜索缩小范围</span>
            </div>
            <div class="flex gap-2">
              <button
                type="button"
                class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                @click="closeAddDialog"
              >取消</button>
              <button
                type="button"
                class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
                :disabled="!candidateSelected.length || adding"
                @click="submitAdd"
              >{{ adding ? '添加中…' : `加入本部门${candidateSelected.length ? `（${candidateSelected.length}）` : ''}` }}</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 重置密码：与用户管理共用同一组件，等保规则与提示完全一致 -->
    <PasswordResetDialog
      v-if="passwordTarget"
      :display-name="passwordTarget.user_name"
      :submitting="submittingPassword"
      hint="保存后该用户的全部登录会话会立即失效"
      @cancel="closePasswordDialog"
      @submit="submitPassword"
    />

    <ConfirmModal
      v-if="showRemoveConfirm"
      title="移出部门成员"
      :message="removeConfirmMessage"
      confirm-text="移出"
      type="danger"
      @confirm="confirmRemove"
      @cancel="cancelRemove"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import axios from '../../utils/axios'
import { useToast } from '../../composables/useToast'
import { extractApiErrorDetail } from '../../utils/apiErrorDetail'
import { maskEmail } from '../../utils/maskEmail'
import ConfirmModal from '../ConfirmModal.vue'
import PasswordResetDialog from '../PasswordResetDialog.vue'

/**
 * 部门成员面板。
 *
 * 与角色成员抽屉的关键差异：这里是**增量**接口（加人 / 移人），不是"提交整集"。
 * 原因是部门成员没有"一次编排整个部门"的使用场景，增量接口也不存在两个管理员
 * 互相覆盖的窗口。
 */
const props = withDefaults(
  defineProps<{
    deptId: number | null
    /** 有没有子部门：没有就不该显示"含子部门"开关 */
    hasChildren?: boolean
    canEdit?: boolean
    canEditUser?: boolean
    canJumpToUsers?: boolean
  }>(),
  { hasChildren: false, canEdit: false, canEditUser: false, canJumpToUsers: false }
)

const emit = defineEmits<{ (e: 'changed'): void }>()

const { showToast } = useToast()
const router = useRouter()

const PAGE_SIZE = 20
const pageSize = PAGE_SIZE

const members = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const search = ref('')
// 部门负责人常关心"整个分支有多少人"，但直属视图同样必要 → 做成开关
const includeSub = ref(false)
const loading = ref(false)
const loadError = ref('')
const acting = ref<number | null>(null)
const selectedIds = ref<number[]>([])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

// 添加成员
const showAddDialog = ref(false)
const candidateSearch = ref('')
const unassignedOnly = ref(false)
const candidates = ref<any[]>([])
const candidateLoading = ref(false)
const candidateSelected = ref<number[]>([])
const adding = ref(false)

// 重置密码
const passwordTarget = ref<any>(null)
const submittingPassword = ref(false)

// 移出确认（项目契约禁止原生 confirm）
const showRemoveConfirm = ref(false)
const pendingRemoveIds = ref<number[]>([])

const removeConfirmMessage = computed(() => {
  const count = pendingRemoveIds.value.length
  return `将 ${count} 人移出本部门。\n\n若被移出的是某人的主部门，会自动从 TA 的其他部门里重选一个；没有其他部门则清空部门代码。`
})

const formatTime = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const otherDepartments = (member: any) =>
  (member.dept_names || []).filter((name: string) => name && name !== currentDeptName.value)

const currentDeptName = ref('')

const allSelected = computed(
  () => members.value.length > 0 && selectedIds.value.length === members.value.length
)

const toggleSelectAll = () => {
  selectedIds.value = allSelected.value ? [] : members.value.map((m) => m.id)
}

const fetchMembers = async () => {
  if (!props.deptId) {
    members.value = []
    total.value = 0
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get(`/api/portal/departments/${props.deptId}/members`, {
      params: {
        page: page.value,
        size: pageSize,
        search: search.value.trim() || undefined,
        include_sub: includeSub.value || undefined
      }
    })
    members.value = response.data.items || []
    total.value = response.data.total || 0
    currentDeptName.value = response.data.dept?.name || ''
    if (members.value.length === 0 && page.value > 1 && total.value > 0) {
      page.value -= 1
      await fetchMembers()
    }
  } catch (e: any) {
    loadError.value = extractApiErrorDetail(e?.response?.data, '成员加载失败')
  } finally {
    loading.value = false
  }
}

const goPage = (next: number) => {
  page.value = next
  selectedIds.value = []
  fetchMembers()
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => props.deptId,
  () => {
    includeSub.value = false
    page.value = 1
  }
)

watch(includeSub, () => {
  page.value = 1
  selectedIds.value = []
  fetchMembers()
})

watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    selectedIds.value = []
    fetchMembers()
  }, 300)
})
onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

watch(
  () => props.deptId,
  (id) => {
    page.value = 1
    search.value = ''
    selectedIds.value = []
    members.value = []
    total.value = 0
    if (id) fetchMembers()
  },
  { immediate: true }
)

const setPrimary = async (member: any) => {
  if (!props.deptId) return
  acting.value = member.id
  try {
    await axios.post(`/api/portal/departments/${props.deptId}/members/primary`, {
      user_id: member.id
    })
    showToast('已设为本部门的主部门', 'success')
    await fetchMembers()
    emit('changed')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '设置失败'), 'error')
  } finally {
    acting.value = null
  }
}

const toggleStatus = async (member: any) => {
  const nextStatus = member.status === 1 ? 0 : 1
  acting.value = member.id
  try {
    await axios.patch(`/api/portal/management/users/${member.id}/status`, { status: nextStatus })
    member.status = nextStatus
    showToast(nextStatus === 1 ? '账号已启用' : '账号已禁用', 'success')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '操作失败'), 'error')
  } finally {
    acting.value = null
  }
}

const requestRemove = (ids: number[]) => {
  if (!props.deptId || !ids.length) return
  // 「含子部门」视图里勾进来的人可能只是子部门成员：移出接口按当前部门 id 校验，
  // 对他们只会移出 0 行，还会因为列表仍包含子树而"看起来没反应"。先过滤掉并说明。
  const directIds = Array.from(new Set(ids)).filter((id) => {
    const member = members.value.find((item: any) => item.id === id)
    return member ? member.is_direct !== false : true
  })
  const blocked = new Set(ids).size - directIds.length
  if (!directIds.length) {
    showToast('选中的成员都属于子部门，请到对应子部门里移出', 'info')
    return
  }
  if (blocked > 0) {
    showToast(`已跳过 ${blocked} 位属于子部门的成员，请到对应子部门操作`, 'info')
  }
  pendingRemoveIds.value = directIds
  showRemoveConfirm.value = true
}

const cancelRemove = () => {
  showRemoveConfirm.value = false
  pendingRemoveIds.value = []
}

const confirmRemove = async () => {
  const ids = [...pendingRemoveIds.value]
  showRemoveConfirm.value = false
  pendingRemoveIds.value = []
  await executeRemove(ids)
}

const executeRemove = async (ids: number[]) => {
  if (!props.deptId || !ids.length) return
  acting.value = ids[0] ?? null
  try {
    const response = await axios.post(
      `/api/portal/departments/${props.deptId}/members/remove`,
      { user_ids: ids }
    )
    const removed = Number(response.data?.removed || 0)
    const skipped = Number(response.data?.skipped || 0)
    showToast(
      `已移出 ${removed} 人${skipped ? `，${skipped} 人本就不在本部门` : ''}`,
      removed ? 'success' : 'info'
    )
    selectedIds.value = []
    await fetchMembers()
    emit('changed')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '移出失败'), 'error')
  } finally {
    acting.value = null
  }
}

const loadCandidates = async () => {
  if (!props.deptId) return
  candidateLoading.value = true
  try {
    const response = await axios.get('/api/portal/management/users', {
      params: {
        page: 1,
        size: 50,
        search: candidateSearch.value.trim() || undefined,
        exclude_dept_id: props.deptId,
        unassigned_only: unassignedOnly.value || undefined
      }
    })
    candidates.value = response.data.items || []
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '候选用户加载失败'), 'error')
  } finally {
    candidateLoading.value = false
  }
}

const onUnassignedOnlyChange = () => {
  // 过滤条件变了，之前勾选的人可能已不在当前列表里：提交"看不见的人"比多选一次更糟
  candidateSelected.value = []
  loadCandidates()
}

// 候选列表的展示辅助：首字母头像 + 已属部门摘要
const AVATAR_CLASSES = [
  'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300',
  'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300',
  'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300',
  'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300',
  'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300',
  'bg-cyan-100 text-cyan-700 dark:bg-cyan-500/20 dark:text-cyan-300'
]

const initialOf = (user: any): string =>
  String(user?.real_name || user?.user_name || '?').trim().slice(0, 1).toUpperCase()

const avatarClass = (user: any): string => {
  const index = Math.abs(Number(user?.id) || 0) % AVATAR_CLASSES.length
  return AVATAR_CLASSES[index] ?? AVATAR_CLASSES[0]!
}

/** 该用户已在哪些部门——避免管理员重复添加时没有任何依据。 */
const deptSummary = (user: any): string => {
  const names: string[] = Array.isArray(user?.dept_names) ? user.dept_names : []
  if (!names.length) return ''
  const shown = names.slice(0, 2).join('、')
  return names.length > 2 ? `${shown} +${names.length - 2}` : shown
}

const allCandidatesSelected = computed(
  () => candidates.value.length > 0 && candidateSelected.value.length === candidates.value.length
)

const toggleCandidateAll = () => {
  candidateSelected.value = allCandidatesSelected.value ? [] : candidates.value.map((user) => user.id)
}

const openAddDialog = () => {
  showAddDialog.value = true
  candidateSearch.value = ''
  unassignedOnly.value = false
  candidateSelected.value = []
  loadCandidates()
}

const closeAddDialog = () => {
  showAddDialog.value = false
  candidates.value = []
  candidateSelected.value = []
}

let candidateTimer: ReturnType<typeof setTimeout> | null = null
watch(candidateSearch, () => {
  if (candidateTimer) clearTimeout(candidateTimer)
  candidateTimer = setTimeout(loadCandidates, 300)
})
onUnmounted(() => {
  if (candidateTimer) clearTimeout(candidateTimer)
})

const submitAdd = async () => {
  if (!props.deptId || !candidateSelected.value.length) return
  adding.value = true
  try {
    const response = await axios.post(`/api/portal/departments/${props.deptId}/members`, {
      user_ids: candidateSelected.value
    })
    const added = Number(response.data?.added || 0)
    showToast(`已添加 ${added} 人${added === 0 ? '（均已在部门中）' : ''}`, added ? 'success' : 'info')
    closeAddDialog()
    await fetchMembers()
    emit('changed')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '添加失败'), 'error')
  } finally {
    adding.value = false
  }
}

const openPasswordDialog = (member: any) => {
  passwordTarget.value = member
}

const closePasswordDialog = () => {
  passwordTarget.value = null
}

/** 密码校验由 PasswordResetDialog 内部完成，这里只负责提交。 */
const submitPassword = async (password: string) => {
  if (!passwordTarget.value?.id) return
  submittingPassword.value = true
  try {
    await axios.post(`/api/portal/management/users/${passwordTarget.value.id}/set-password`, {
      password
    })
    showToast('密码已重置，该用户的登录会话已全部失效', 'success')
    closePasswordDialog()
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '密码设置失败'), 'error')
  } finally {
    submittingPassword.value = false
  }
}

const jumpToUser = (member: any) => {
  router.push({ path: '/dashboard/users', query: { q: member.user_name } })
}

/** 直接进入该用户的编辑界面：带上 edit，用户页落地后自动打开那一行的编辑框。
 *
 * 不在本页内嵌编辑弹窗——用户编辑表单牵着权限、额度、Agent 限定一大串状态，
 * 抽成公用组件等于把用户管理重写一遍，还会长出两套逐渐分叉的编辑逻辑。
 */
const openUserEditor = (member: any) => {
  router.push({
    path: '/dashboard/users',
    query: { q: member.user_name, edit: String(member.id) },
  })
}

defineExpose({ fetchMembers })
</script>
