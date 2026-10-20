<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[9990]">
      <!-- 遮罩：点击即请求关闭（关闭本身不做拦截，抽屉里没有"未保存"状态） -->
      <div class="absolute inset-0 bg-black/50 backdrop-blur-[1px]" @click="requestClose" aria-hidden="true"></div>

      <aside
        class="absolute top-0 right-0 h-full bg-white shadow-2xl flex flex-col overflow-hidden transition-transform"
        :class="isMobile ? 'w-full' : 'w-[min(720px,100vw)] rounded-l-2xl'"
        role="dialog"
        aria-modal="true"
        aria-labelledby="role-members-title"
      >
        <!-- 头部：角色身份与成员规模 -->
        <header class="px-4 sm:px-5 py-4 border-b border-gray-100 flex items-start justify-between gap-3 shrink-0">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h2 id="role-members-title" class="text-lg font-black text-gray-900 truncate">
                {{ role?.name || '角色成员' }}
              </h2>
              <span
                v-if="isSystemRole"
                class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-slate-100 text-slate-600"
              >系统</span>
              <span
                v-if="isRemovalLocked"
                class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-amber-50 text-amber-700 border border-amber-200"
                title="小程序静默登录会自动重新加入该角色，此处移出无效"
              >移出无效</span>
            </div>
            <p class="mt-1 flex items-center gap-2 text-xs text-gray-400 flex-wrap">
              <span class="font-mono bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-[10px]">{{ role?.code }}</span>
              <span>共 <b class="text-gray-700 font-semibold">{{ total }}</b> 位成员</span>
            </p>
          </div>
          <button
            type="button"
            @click="requestClose"
            class="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 transition-colors shrink-0"
            aria-label="关闭角色成员抽屉"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <!-- 工具条：搜索 + 添加 + 批量移出 -->
        <div class="px-4 sm:px-5 py-3 border-b border-gray-100 space-y-2 shrink-0">
          <div class="flex items-center gap-2">
            <div class="relative flex-1 min-w-0">
              <input
                v-model="search"
                @input="onSearchInput"
                type="search"
                placeholder="搜索姓名或账号…"
                aria-label="搜索角色成员"
                class="w-full bg-white border border-gray-200 rounded-xl px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
              />
              <svg class="w-4 h-4 text-gray-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <button
              v-if="canEditRole"
              type="button"
              @click="emit('add-members', role)"
              class="shrink-0 px-3 py-2 rounded-xl bg-blue-600 text-white text-xs font-bold hover:bg-blue-700 transition-colors"
            >+ 添加成员</button>
          </div>
          <div v-if="canEditRole && selectedIds.length > 0" class="flex items-center justify-between gap-2">
            <span class="text-[11px] text-gray-500">已选 {{ selectedIds.length }} 人（仅当前页）</span>
            <button
              type="button"
              @click="removeMembers(selectedIds)"
              :disabled="acting !== null || isRemovalLocked"
              :title="isRemovalLocked ? '小程序角色不支持移出成员' : ''"
              class="px-3 py-1.5 rounded-lg border border-red-200 text-red-600 text-[11px] font-bold hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >批量移出</button>
          </div>
        </div>

        <!-- 成员列表 -->
        <div class="flex-1 overflow-y-auto custom-scrollbar">
          <div v-if="loading" class="py-20 text-center text-xs text-gray-400 animate-pulse">正在加载成员…</div>

          <!-- 加载失败不能渲染成空列表：那会被误读成"该角色没有成员" -->
          <div v-else-if="loadError" class="py-16 px-6 text-center">
            <p class="text-sm font-semibold text-red-600">成员加载失败</p>
            <p class="mt-1 text-xs text-gray-500">{{ loadError }}</p>
            <button
              type="button"
              @click="fetchMembers()"
              class="mt-3 px-3 py-1.5 rounded-lg border border-gray-300 text-xs font-semibold hover:bg-gray-50"
            >重试</button>
          </div>

          <div v-else-if="members.length === 0" class="py-16 px-6 text-center">
            <p class="text-sm text-gray-500">
              <template v-if="search.trim()">没有匹配「{{ search.trim() }}」的成员</template>
              <template v-else>该角色还没有成员</template>
            </p>
            <button
              v-if="canEditRole && !search.trim()"
              type="button"
              @click="emit('add-members', role)"
              class="mt-3 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-700"
            >+ 添加成员</button>
          </div>

          <ul v-else class="divide-y divide-gray-100">
            <li
              v-for="member in members"
              :key="member.id"
              class="px-4 sm:px-5 py-3 flex items-start gap-3 hover:bg-gray-50/70 transition-colors"
            >
              <label v-if="canEditRole" class="pt-1.5 shrink-0">
                <input
                  type="checkbox"
                  :value="member.id"
                  v-model="selectedIds"
                  :disabled="isRemovalLocked"
                  :aria-label="`选择成员 ${member.user_name}`"
                  class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-40"
                />
              </label>

              <div class="w-8 h-8 rounded-full bg-blue-50 text-blue-600 border border-blue-100 flex items-center justify-center font-bold text-[11px] shrink-0">
                {{ (member.real_name || member.user_name || '?').slice(0, 1) }}
              </div>

              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-sm font-semibold text-gray-900 truncate">
                    {{ member.real_name || member.user_name }}
                  </span>
                  <span class="text-[10px] font-mono text-gray-400">@{{ member.user_name }}</span>
                  <span :class="statusClass(member)" class="shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded-full">
                    {{ statusText(member) }}
                  </span>
                </div>

                <p class="mt-0.5 text-[11px] text-gray-400 truncate">
                  {{ member.email || '未填写邮箱' }}
                  <span v-if="member.last_login_at"> · 上次登录 {{ formatDateTime(member.last_login_at) }}</span>
                  <span v-else> · 从未登录</span>
                </p>

                <!-- 多角色是并集：必须让管理员看到"这个人还属于哪些角色" -->
                <div class="mt-1 flex items-center gap-1 flex-wrap text-[10px] text-gray-400">
                  <template v-if="otherRoleNames(member).length">
                    <span>还属于</span>
                    <span
                      v-for="name in otherRoleNames(member).slice(0, 2)"
                      :key="name"
                      class="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 font-semibold"
                    >{{ name }}</span>
                    <span
                      v-if="otherRoleNames(member).length > 2"
                      class="px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-semibold"
                      :title="otherRoleNames(member).join('、')"
                    >+{{ otherRoleNames(member).length - 2 }}</span>
                  </template>
                  <span v-else class="text-gray-300">仅此角色</span>
                </div>
              </div>

              <div class="flex flex-col items-end gap-1 shrink-0">
                <div class="flex items-center gap-1">
                  <button
                    v-if="canEditUser && member.status !== 2"
                    type="button"
                    @click="toggleStatus(member)"
                    :disabled="acting === member.id"
                    :title="member.status === 0 ? '启用该账号' : '禁用该账号'"
                    class="px-2 py-1 rounded-lg text-[10px] font-bold border transition-colors disabled:opacity-40"
                    :class="member.status === 0
                      ? 'border-emerald-200 text-emerald-600 hover:bg-emerald-50'
                      : 'border-gray-200 text-gray-600 hover:bg-gray-50'"
                  >{{ member.status === 0 ? '启用' : '禁用' }}</button>

                  <button
                    v-if="canEditUser"
                    type="button"
                    @click="openPasswordDialog(member)"
                    :disabled="acting === member.id"
                    class="px-2 py-1 rounded-lg text-[10px] font-bold border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  >重置密码</button>

                  <button
                    v-if="canEditRole && !isRemovalLocked"
                    type="button"
                    @click="removeMembers([member.id])"
                    :disabled="acting === member.id"
                    class="px-2 py-1 rounded-lg text-[10px] font-bold border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-40"
                  >移出</button>

                  <button
                    v-if="canJumpToUsers"
                    type="button"
                    @click="jumpToUser(member)"
                    class="px-2 py-1 rounded-lg text-[10px] font-bold border border-gray-200 text-gray-500 hover:bg-gray-50"
                    :title="`到用户管理查看 ${member.user_name}`"
                  >↗</button>
                </div>
                <span class="text-[9px] text-gray-300 font-mono">{{ formatDate(member.created_at) }} 加入</span>
              </div>
            </li>
          </ul>
        </div>

        <!-- 分页 -->
        <footer class="px-4 sm:px-5 py-3 border-t border-gray-100 flex items-center justify-between gap-2 shrink-0">
          <span class="text-[11px] text-gray-500">
            共 {{ total }} 人 · 第 {{ page }}/{{ totalPages }} 页
          </span>
          <div class="flex gap-2">
            <button
              type="button"
              @click="goPage(page - 1)"
              :disabled="page <= 1 || loading"
              class="px-3 py-1.5 border border-gray-300 rounded-lg text-xs font-medium hover:bg-gray-50 disabled:opacity-40"
            >上一页</button>
            <button
              type="button"
              @click="goPage(page + 1)"
              :disabled="page >= totalPages || loading"
              class="px-3 py-1.5 border border-gray-300 rounded-lg text-xs font-medium hover:bg-gray-50 disabled:opacity-40"
            >下一页</button>
          </div>
        </footer>
      </aside>
    </div>
  </Teleport>

  <!-- 系统角色移出确认：走页面内弹窗，不用浏览器原生 confirm（项目契约禁止） -->
  <ConfirmModal
    v-if="showRemovalConfirm"
    :title="`从「${role?.name || ''}」移出成员？`"
    :message="removalConfirmMessage"
    confirm-text="确认移出"
    type="danger"
    @confirm="confirmRemoval"
    @cancel="cancelRemoval"
  />

  <!-- 重置密码：层级高于抽屉，避免被遮盖 -->
  <Teleport to="body">
    <div
      v-if="showPasswordDialog"
      class="fixed inset-0 z-[9995] bg-black/50 flex items-center justify-center p-4"
      @click.self="closePasswordDialog"
    >
      <form @submit.prevent="submitPassword" class="bg-white rounded-xl shadow-2xl w-full max-w-sm p-5">
        <h3 class="text-base font-bold text-gray-900">重置登录密码</h3>
        <p class="mt-1 text-xs text-gray-500">
          为 <b class="text-gray-800">{{ passwordTarget?.user_name }}</b> 设置新密码。
          提交后该用户<b class="text-red-600">当前所有登录会话会立即失效</b>，需要用新密码重新登录。
        </p>
        <div class="mt-4 space-y-3">
          <input
            v-model="passwordForm.password"
            type="password"
            autocomplete="new-password"
            placeholder="新密码（8-32 位，含大小写/数字/符号中的三类）"
            class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <input
            v-model="passwordForm.confirm"
            type="password"
            autocomplete="new-password"
            placeholder="再次输入新密码"
            class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            @click="closePasswordDialog"
            class="px-3 py-2 rounded-lg border border-gray-300 text-xs font-semibold hover:bg-gray-50"
          >取消</button>
          <button
            type="submit"
            :disabled="submittingPassword"
            class="px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-700 disabled:opacity-50"
          >{{ submittingPassword ? '提交中…' : '确认重置' }}</button>
        </div>
      </form>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import axios from '../../utils/axios'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import ConfirmModal from '../ConfirmModal.vue'
import { checkPasswordPolicy } from '../../utils/passwordPolicy'
import { extractApiErrorDetail } from '../../utils/apiErrorDetail'

const props = defineProps<{
  modelValue: boolean
  role: Record<string, any> | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'changed'): void
  (e: 'add-members', role: Record<string, any> | null): void
}>()

const router = useRouter()
const { showToast } = useToast()
const { hasPermission } = useUser()

// 成员关系归角色权限，账号状态归用户权限：两者必须分开收敛，
// 否则只有角色权限的管理员会看到"禁用"按钮然后吃 403。
const canEditRole = computed(() => hasPermission('element:role:edit'))
const canEditUser = computed(() => hasPermission('element:user:edit'))
const canJumpToUsers = computed(() => hasPermission('menu:system:users'))

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
const handleResize = () => { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', handleResize))
onUnmounted(() => window.removeEventListener('resize', handleResize))

const PAGE_SIZE = 50

const members = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const search = ref('')
const loading = ref(false)
const loadError = ref('')
const acting = ref<number | null>(null)
const selectedIds = ref<number[]>([])
// 系统角色移出确认（页面内弹窗，不用原生 confirm）
const showRemovalConfirm = ref(false)
const pendingRemovalIds = ref<number[]>([])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

// 系统角色口径与 roles.py 的 SYSTEM_ROLE_CODES、Roles.vue 的 isSystemRole() 保持一致
const roleCode = computed(() => String(props.role?.code || '').toLowerCase())
const isSystemRole = computed(() =>
  ['default', 'admin', 'system', 'miniprogram_user'].includes(roleCode.value)
)
// 小程序角色由静默登录自动写入，移出后下次登录又被加回来，所以直接锁掉
const isRemovalLocked = computed(() => roleCode.value === 'miniprogram_user')
// 其余系统角色允许移出，但要让管理员知道后果
const needsRemovalConfirm = computed(() =>
  ['default', 'admin', 'system'].includes(roleCode.value)
)

const statusText = (member: any) => {
  if (member.status === 0) return '已停用'
  if (member.status === 2) return '待审核'
  return '已启用'
}
const statusClass = (member: any) => {
  if (member.status === 0) return 'bg-gray-100 text-gray-500'
  if (member.status === 2) return 'bg-amber-50 text-amber-700'
  return 'bg-emerald-50 text-emerald-700'
}

/** 该用户除当前角色外还属于哪些角色（多角色并集语义，必须可见） */
const otherRoleNames = (member: any): string[] => {
  const selfName = String(props.role?.name || '')
  return (member.role_names || []).filter((name: string) => name !== selfName)
}

const formatDate = (value?: string | null) => {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${formatDate(value)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const fetchMembers = async () => {
  if (!props.role?.id) return
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get(`/api/portal/roles/${props.role.id}/users`, {
      params: {
        page: page.value,
        size: PAGE_SIZE,
        search: search.value.trim() || undefined,
      },
    })
    members.value = response.data.items || []
    total.value = response.data.total || 0
    // 移出或筛选后当前页可能空掉（第 2 页最后一人被移出就是这种情况）：
    // 直接停在空页会让管理员以为"成员没了"，这里回退一页再拉一次。
    if (members.value.length === 0 && page.value > 1 && total.value > 0) {
      page.value -= 1
      await fetchMembers()
      return
    }
  } catch (e: any) {
    // 失败必须显式失败：渲染成空列表会被误读成"该角色没有成员"
    loadError.value = extractApiErrorDetail(e?.response?.data, '请检查网络或稍后重试')
    members.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
const onSearchInput = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    selectedIds.value = []
    fetchMembers()
  }, 300)
}

const goPage = (target: number) => {
  if (target < 1 || target > totalPages.value || loading.value) return
  page.value = target
  // 选中只作用于当前页：跨页全选会让"批量移出"退化成一次整集替换
  if (selectedIds.value.length) {
    selectedIds.value = []
    showToast('已切换页面，选中已清空', 'info')
  }
  fetchMembers()
}

const requestClose = () => emit('update:modelValue', false)

const removalConfirmMessage = computed(() => {
  const count = pendingRemovalIds.value.length
  return (
    `本次将移出 ${count} 人。\n\n` +
    '这是系统角色：移出后，如果该用户不再属于其他角色，将没有任何业务角色。'
  )
})

const cancelRemoval = () => {
  showRemovalConfirm.value = false
  pendingRemovalIds.value = []
}

const confirmRemoval = async () => {
  const ids = [...pendingRemovalIds.value]
  showRemovalConfirm.value = false
  pendingRemovalIds.value = []
  await executeRemove(ids)
}

/**
 * 移出入口。系统角色先弹页面内确认（原生 confirm 被项目契约禁止），
 * 普通角色直接执行——单点移出没有"误操作代价大"的问题，加确认反而拖慢。
 */
const removeMembers = async (ids: number[]) => {
  if (!props.role?.id || !ids?.length) return
  if (isRemovalLocked.value) {
    showToast('小程序角色由静默登录自动维护，不支持移出成员', 'warning')
    return
  }
  const uniqueIds = Array.from(new Set(ids))
  if (needsRemovalConfirm.value) {
    pendingRemovalIds.value = uniqueIds
    showRemovalConfirm.value = true
    return
  }
  await executeRemove(uniqueIds)
}

const executeRemove = async (uniqueIds: number[]) => {
  const roleId = props.role?.id
  if (!roleId || !uniqueIds.length) return

  acting.value = uniqueIds[0] ?? null
  try {
    const response = await axios.post(
      `/api/portal/roles/${roleId}/users/remove`,
      { user_ids: uniqueIds }
    )
    const removed = Number(response.data?.removed || 0)
    const skipped = Number(response.data?.skipped || 0)
    const suffix = skipped > 0 ? `，${skipped} 人本就不在该角色中` : ''
    showToast(`已移出 ${removed} 人${suffix}`, removed > 0 ? 'success' : 'info')
    selectedIds.value = []
    await fetchMembers()
    emit('changed')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '移出失败'), 'error')
  } finally {
    acting.value = null
  }
}

const toggleStatus = async (member: any) => {
  const nextStatus = member.status === 0 ? 1 : 0
  acting.value = member.id
  try {
    await axios.patch(`/api/portal/management/users/${member.id}/status`, {
      status: nextStatus,
    })
    member.status = nextStatus
    showToast(nextStatus === 1 ? '账号已启用' : '账号已禁用', 'success')
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '状态更新失败'), 'error')
  } finally {
    acting.value = null
  }
}

const jumpToUser = (member: any) => {
  // 只预填搜索，不自动打开编辑弹窗：那需要先按 id 取回完整用户对象，收益不值
  router.push({ path: '/dashboard/users', query: { q: member.user_name } })
}

// --- 重置密码 ---
const showPasswordDialog = ref(false)
const passwordTarget = ref<any>(null)
const passwordForm = ref({ password: '', confirm: '' })
const submittingPassword = ref(false)

const openPasswordDialog = (member: any) => {
  passwordTarget.value = member
  passwordForm.value = { password: '', confirm: '' }
  showPasswordDialog.value = true
}
const closePasswordDialog = () => {
  showPasswordDialog.value = false
  passwordTarget.value = null
  passwordForm.value = { password: '', confirm: '' }
  submittingPassword.value = false
}

const submitPassword = async () => {
  if (!passwordTarget.value?.id) return
  const policy = checkPasswordPolicy(passwordForm.value.password, passwordTarget.value.user_name)
  if (!policy.valid) {
    showToast(policy.message, 'warning')
    return
  }
  if (passwordForm.value.password !== passwordForm.value.confirm) {
    showToast('两次输入的密码不一致', 'warning')
    return
  }
  submittingPassword.value = true
  try {
    await axios.post(
      `/api/portal/management/users/${passwordTarget.value.id}/set-password`,
      { password: passwordForm.value.password }
    )
    showToast('密码已重置，该用户的登录会话已全部失效', 'success')
    closePasswordDialog()
  } catch (e: any) {
    showToast(extractApiErrorDetail(e?.response?.data, '密码设置失败'), 'error')
  } finally {
    submittingPassword.value = false
  }
}

watch(
  () => [props.modelValue, props.role?.id] as const,
  ([open]) => {
    if (!open) {
      selectedIds.value = []
      closePasswordDialog()
      return
    }
    // 打开或切换角色：重置全部视图状态后只拉一次
    page.value = 1
    search.value = ''
    selectedIds.value = []
    members.value = []
    total.value = 0
    loadError.value = ''
    showRemovalConfirm.value = false
    pendingRemovalIds.value = []
    fetchMembers()
  }
)
</script>
