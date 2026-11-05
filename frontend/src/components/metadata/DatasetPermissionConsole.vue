<script setup lang="ts">
/**
 * 数据集权限控制台：数据集级（谁能进）→ 表级（能看哪些表）→ 行级（能看哪些行）。
 *
 * 设计要点（与 prototypes/2026-11-05-dataset-permission-console.html 一致）：
 * - 一条授权 = 主体（全员/角色/用户 多选）× 表范围（全部/指定）× 行级策略 × 有效期；
 * - 用户级覆盖角色级（可以按人收窄）；「全员」是公共基线，不能被收窄；
 * - 不提供「排除某几张表」：新增表默认可见，靠「待确认」提示兜住，
 *   白名单是 fail-closed 的，黑名单一旦加表就会漏；
 * - 合并语义只在后端实现一次，前端「权限模拟」直接读后端结果，避免两套口径。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import axios from '../../utils/axios'
import { useToast } from '../../composables/useToast'

type ScopeMode = 'all' | 'list'
type RowPolicy = 'inherit' | 'none' | 'custom'
type SubjectKind = 'all' | 'role' | 'user'

interface TableMeta {
  id: number
  physical_name: string
  term?: string | null
}

interface Grant {
  key: string
  kind: SubjectKind
  id?: number
  label: string
  sub?: string
  mode: ScopeMode
  tables: number[]
  rowPolicy: RowPolicy
  rowCondition?: string | null
  expireAt?: string | null
  status: 'active' | 'expired'
}

const props = defineProps<{
  datasetId: number
  datasetName?: string
  canEdit: boolean
  /** 数据集是否启用了精细化行级校验（读 dataset.enable_data_perm） */
  rowFilterEnabled?: boolean
  /** 数据集自身的行级配置（读 dataset.row_filter_config） */
  rowFilterConfig?: Record<string, any>
}>()

const emit = defineEmits<{ (e: 'changed'): void }>()

const { showToast } = useToast()

const loading = ref(true)
const saving = ref(false)
const scopeSupported = ref(true)
const grants = ref<Grant[]>([])
const tables = ref<TableMeta[]>([])
const candidates = ref<{ roles: any[]; users: any[] }>({ roles: [], users: [] })

const view = ref<'subjects' | 'tables' | 'rows' | 'simulate'>('subjects')

const allMembersGrant = computed(() => grants.value.find((g) => g.kind === 'all') || null)
const roleGrants = computed(() => grants.value.filter((g) => g.kind === 'role'))
const userGrants = computed(() => grants.value.filter((g) => g.kind === 'user'))
const activeGrants = computed(() => grants.value.filter((g) => g.status === 'active'))
const expiredCount = computed(() => grants.value.filter((g) => g.status === 'expired').length)

const tableName = (id: number) => {
  const hit = tables.value.find((t) => t.id === id)
  return hit?.physical_name || `#${id}`
}

/** 表级概览：显式限定范围的条目 + 去重后的表数（不在这里复刻「用户覆盖角色」的合并） */
const tableScopeStats = computed(() => {
  const listGrants = activeGrants.value.filter((g) => g.mode === 'list')
  const covered = new Set<number>()
  listGrants.forEach((g) => g.tables.forEach((id) => covered.add(id)))
  const allCount = activeGrants.value.filter((g) => g.mode === 'all').length
  // 有「全部表」授权时每张表都被覆盖，不存在「未被任何授权覆盖」的表；
  // 只有纯白名单场景才需要提示「某些表没被点名」（新增表不会自动进白名单）。
  const uncovered =
    allCount > 0 ? [] : tables.value.filter((t) => !covered.has(t.id))
  return {
    listCount: listGrants.length,
    allCount,
    covered: allCount > 0 ? tables.value.length : covered.size,
    uncovered: uncovered.length,
    uncoveredTables: uncovered,
  }
})

const rowPolicyStats = computed(() => {
  const base = activeGrants.value
  return {
    inherit: base.filter((g) => g.rowPolicy === 'inherit').length,
    none: base.filter((g) => g.rowPolicy === 'none').length,
    custom: base.filter((g) => g.rowPolicy === 'custom').length,
  }
})

const subjectCount = computed(() => activeGrants.value.length)

/** 没有任何条目显式覆盖的表：只有存在「全部表」授权时才可能可见 */
const pendingTables = computed(() => tableScopeStats.value.uncoveredTables)

const hasPendingConfirm = computed(
  () => pendingTables.value.length > 0 && tableScopeStats.value.listCount > 0,
)

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

const load = async () => {
  loading.value = true
  try {
    const res = await axios.get(`/api/portal/metadata/datasets/${props.datasetId}/permissions`)
    const data = res.data?.data || {}
    scopeSupported.value = data.scope_supported !== false
    tables.value = Array.isArray(data.tables) ? data.tables : []

    const collected: Grant[] = []
    const toGrant = (raw: any, kind: SubjectKind): Grant => ({
      key: `${kind}:${raw.id ?? 'all'}`,
      kind,
      id: raw.id,
      label:
        kind === 'all'
          ? '全员'
          : kind === 'role'
            ? String(raw.name || raw.code || `角色 ${raw.id}`)
            : String(raw.real_name || raw.user_name || `用户 ${raw.id}`),
      sub:
        kind === 'role'
          ? String(raw.code || '')
          : kind === 'user'
            ? String(raw.user_name || '')
            : '所有能进入该数据集的成员',
      mode: raw?.scope?.mode === 'list' ? 'list' : 'all',
      tables: Array.isArray(raw?.scope?.tables) ? raw.scope.tables.map((n: any) => Number(n)) : [],
      rowPolicy: (['inherit', 'none', 'custom'] as RowPolicy[]).includes(raw?.row_policy)
        ? (raw.row_policy as RowPolicy)
        : 'inherit',
      rowCondition: raw?.row_condition || null,
      expireAt: raw?.expire_at || null,
      status: raw?.status === 'expired' ? 'expired' : 'active',
    })

    if (data.all_members) collected.push(toGrant(data.all_members, 'all'))
    ;(data.roles || []).forEach((r: any) => collected.push(toGrant(r, 'role')))
    ;(data.users || []).forEach((u: any) => collected.push(toGrant(u, 'user')))
    grants.value = collected

    if (props.canEdit && !candidates.value.roles.length && !candidates.value.users.length) {
      const cand = await axios.get('/api/portal/metadata/candidates')
      candidates.value = cand.data?.data || { roles: [], users: [] }
    }
  } catch (err) {
    console.error('加载数据集授权失败:', err)
    showToast('加载授权配置失败', 'error')
  } finally {
    loading.value = false
  }
}

onMounted(load)

// 同一个 tab 内切换数据集时组件不会重建，必须显式重载，否则会继续显示上一个数据集的授权。
watch(
  () => props.datasetId,
  () => {
    // 报告是「某人在某数据集上看到什么」，切数据集后旧结论必须作废。
    simResult.value = null
    void load()
  },
)

// ---------------------------------------------------------------------------
// 抽屉：新增 / 编辑
// ---------------------------------------------------------------------------

const drawerOpen = ref(false)
const drawerStep = ref(0)
const editingKey = ref<string | null>(null)
const subjectSearch = ref('')
const tableSearch = ref('')

const draft = reactive({
  kind: 'role' as SubjectKind,
  roleIds: [] as number[],
  userIds: [] as number[],
  scopeMode: 'all' as ScopeMode,
  tableIds: [] as number[],
  rowPolicy: 'inherit' as RowPolicy,
  rowCondition: '',
  expiryMode: 'never' as 'never' | 'date',
  expireAt: '',
})

const resetDraft = () => {
  drawerStep.value = 0
  editingKey.value = null
  subjectSearch.value = ''
  tableSearch.value = ''
  draft.kind = 'role'
  draft.roleIds = []
  draft.userIds = []
  draft.scopeMode = 'all'
  draft.tableIds = []
  draft.rowPolicy = 'inherit'
  draft.rowCondition = ''
  draft.expiryMode = 'never'
  draft.expireAt = ''
}

const openCreate = () => {
  resetDraft()
  drawerOpen.value = true
}

const openEdit = (grant: Grant) => {
  resetDraft()
  editingKey.value = grant.key
  draft.kind = grant.kind
  if (grant.kind === 'role' && grant.id != null) draft.roleIds = [grant.id]
  if (grant.kind === 'user' && grant.id != null) draft.userIds = [grant.id]
  draft.scopeMode = grant.mode
  draft.tableIds = [...grant.tables]
  draft.rowPolicy = grant.rowPolicy
  draft.rowCondition = grant.rowCondition || ''
  draft.expiryMode = grant.expireAt ? 'date' : 'never'
  draft.expireAt = grant.expireAt ? String(grant.expireAt).slice(0, 10) : ''
  drawerOpen.value = true
}

const closeDrawer = () => {
  drawerOpen.value = false
  resetDraft()
}

const filteredRoles = computed(() => {
  const q = subjectSearch.value.trim().toLowerCase()
  const existing = new Set(roleGrants.value.map((g) => g.id))
  // 编辑态只保留当前主体：否则在这里补勾另一个角色，提交就变成「顺手给别人也授权了」，
  // 而「编辑」的预期是只改这一条的范围/行级/有效期。
  const editable = (candidates.value.roles || []).filter(
    (r: any) => !editingKey.value || draft.roleIds.includes(Number(r.id)),
  )
  return editable
    .filter((r: any) => !existing.has(Number(r.id)) || draft.roleIds.includes(Number(r.id)))
    .filter((r: any) =>
      !q
        ? true
        : String(r.name || '').toLowerCase().includes(q) ||
          String(r.code || '').toLowerCase().includes(q),
    )
})

const filteredUsers = computed(() => {
  const q = subjectSearch.value.trim().toLowerCase()
  const existing = new Set(userGrants.value.map((g) => g.id))
  const editable = (candidates.value.users || []).filter(
    (u: any) => !editingKey.value || draft.userIds.includes(Number(u.id)),
  )
  return editable
    .filter((u: any) => !existing.has(Number(u.id)) || draft.userIds.includes(Number(u.id)))
    .filter((u: any) =>
      !q
        ? true
        : String(u.real_name || '').toLowerCase().includes(q) ||
          String(u.user_name || '').toLowerCase().includes(q),
    )
})

const filteredTables = computed(() => {
  const q = tableSearch.value.trim().toLowerCase()
  if (!q) return tables.value
  return tables.value.filter(
    (t) =>
      t.physical_name.toLowerCase().includes(q) ||
      String(t.term || '').toLowerCase().includes(q),
  )
})

const toggle = (list: number[], id: number) => {
  const idx = list.indexOf(id)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(id)
}

const toggleDraftSubject = (kind: 'role' | 'user', id: number) => {
  toggle(kind === 'role' ? draft.roleIds : draft.userIds, id)
}

const toggleDraftTable = (id: number) => toggle(draft.tableIds, id)
const selectAllTables = () => {
  draft.tableIds = tables.value.map((t) => t.id)
}
const clearTables = () => {
  draft.tableIds = []
}

const draftSubjects = computed(() => {
  if (draft.kind === 'all') return [{ type: 'all' as SubjectKind, id: undefined as number | undefined }]
  if (draft.kind === 'role') return draft.roleIds.map((id) => ({ type: 'role' as SubjectKind, id }))
  return draft.userIds.map((id) => ({ type: 'user' as SubjectKind, id }))
})

const canNext = computed(() => {
  if (drawerStep.value === 0) return draftSubjects.value.length > 0
  if (drawerStep.value === 1) return draft.scopeMode === 'all' || draft.tableIds.length > 0
  if (drawerStep.value === 2) {
    if (draft.rowPolicy !== 'custom') return true
    return draft.rowCondition.trim().length > 0
  }
  if (drawerStep.value === 3) return draft.expiryMode === 'never' || !!draft.expireAt
  return true
})

const draftSummary = computed(() => {
  const scopeText =
    draft.scopeMode === 'all' ? '全部表' : `${draft.tableIds.length} 张表`
  const policyText =
    draft.rowPolicy === 'inherit'
      ? '行级继承数据集策略'
      : draft.rowPolicy === 'none'
        ? '不做行级过滤'
        : '自定义行级条件'
  const expiryText = draft.expiryMode === 'never' ? '长期有效' : `到期 ${draft.expireAt}`
  return `${draftSubjects.value.length} 个主体 · ${scopeText} · ${policyText} · ${expiryText}`
})

const submitDrawer = async () => {
  if (!canNext.value) return
  saving.value = true
  try {
    const base = {
      scope: {
        mode: draft.scopeMode,
        tables: draft.scopeMode === 'list' ? [...draft.tableIds] : [],
      },
      row_policy: draft.rowPolicy,
      row_condition: draft.rowPolicy === 'custom' ? draft.rowCondition.trim() : null,
      expire_at: draft.expiryMode === 'date' && draft.expireAt ? `${draft.expireAt}T23:59:59` : null,
    }
    const subjects = draftSubjects.value.map((s) => ({
      target_type: s.type,
      target_id: s.id ?? null,
      ...base,
    }))
    const res = await axios.post(
      `/api/portal/metadata/datasets/${props.datasetId}/permissions`,
      { subjects },
    )
    if (res.data?.warning) showToast(String(res.data.warning), 'warning')
    else showToast(editingKey.value ? '授权已更新' : '授权已添加', 'success')
    closeDrawer()
    await load()
    emit('changed')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    showToast(typeof detail === 'string' ? detail : '保存授权失败', 'error')
  } finally {
    saving.value = false
  }
}

const removeGrant = async (grant: Grant) => {
  if (!confirm(`确认移除「${grant.label}」在该数据集上的授权？`)) return
  try {
    await axios.delete(`/api/portal/metadata/datasets/${props.datasetId}/permissions`, {
      data: {
        target_type: grant.kind,
        target_id: grant.kind === 'all' ? null : grant.id,
      },
    })
    showToast('授权已移除', 'success')
    await load()
    emit('changed')
  } catch (err) {
    showToast('移除授权失败', 'error')
  }
}

// ---------------------------------------------------------------------------
// 权限模拟
// ---------------------------------------------------------------------------

const simUserId = ref<number | null>(null)
const simLoading = ref(false)
const simResult = ref<any | null>(null)

const runSimulate = async () => {
  if (!simUserId.value) return
  simLoading.value = true
  simResult.value = null
  try {
    const res = await axios.get(
      `/api/portal/metadata/datasets/${props.datasetId}/permissions/simulate`,
      { params: { user_id: simUserId.value } },
    )
    simResult.value = res.data?.data || null
  } catch (err) {
    showToast('权限模拟失败', 'error')
  } finally {
    simLoading.value = false
  }
}

const simVisibleTables = computed(() => simResult.value?.visible_tables || [])
const simGrantRows = computed(() => simResult.value?.grants || [])
const simExcludedTables = computed(() => {
  if (!simResult.value) return []
  const visible = new Set(
    (simResult.value.visible_tables || []).map((t: any) => Number(t.id)),
  )
  return tables.value.filter((t) => !visible.has(t.id))
})

const rowPolicyText = (policy: string) =>
  policy === 'none' ? '不做行级过滤' : policy === 'custom' ? '自定义行级条件' : '继承数据集策略'

const scopeText = (grant: Grant) =>
  grant.mode === 'all' ? '全部表' : `${grant.tables.length} 张表`

const rowFilterCfg = computed<Record<string, any>>(() =>
  props.rowFilterConfig && typeof props.rowFilterConfig === 'object' ? props.rowFilterConfig : {},
)

const extractConditions = (rules: any): string[] => {
  if (!Array.isArray(rules)) return []
  return rules
    .map((rule: any) => (typeof rule?.condition === 'string' ? rule.condition.trim() : ''))
    .filter(Boolean)
}

/** 数据集自身策略：用户例外 / 角色策略 / 默认规则（只读，编辑入口在「数据权限配置」） */
const datasetUserExceptions = computed(() => {
  const bucket = rowFilterCfg.value.user_overrides
  if (!bucket || typeof bucket !== 'object') return []
  return Object.entries(bucket)
    .map(([key, rules]) => {
      const grant = userGrants.value.find((g) => String(g.id) === String(key))
      return { key, label: grant?.label || `用户 ${key}`, conditions: extractConditions(rules) }
    })
    .filter((group) => group.conditions.length > 0)
})

const datasetRolePolicies = computed(() => {
  const bucket = rowFilterCfg.value.role_policies
  if (!bucket || typeof bucket !== 'object') return []
  return Object.entries(bucket)
    .map(([code, rules]) => {
      const grant = roleGrants.value.find((g) => g.sub === code)
      return { code, label: grant?.label || code, conditions: extractConditions(rules) }
    })
    .filter((group) => group.conditions.length > 0)
})

const datasetDefaultRules = computed(() => extractConditions(rowFilterCfg.value.default_policy))

const datasetRowRuleCount = computed(
  () =>
    datasetUserExceptions.value.reduce((sum, g) => sum + g.conditions.length, 0) +
    datasetRolePolicies.value.reduce((sum, g) => sum + g.conditions.length, 0) +
    datasetDefaultRules.value.length,
)

const userExceptionCount = (userId?: number) =>
  userId == null ? 0 : extractConditions(rowFilterCfg.value.user_overrides?.[String(userId)]).length

const rolePolicyCount = (roleCode?: string) =>
  roleCode ? extractConditions(rowFilterCfg.value.role_policies?.[roleCode]).length : 0

const allViewTabs = [
  { key: 'subjects', label: '按主体' },
  { key: 'tables', label: '按表' },
  { key: 'rows', label: '行级策略' },
  { key: 'simulate', label: '权限模拟' },
] as const

// 权限模拟要调管理端接口（需要 element:metadata:edit），只读用户看不到这个 tab，
// 否则点进去只会拿到 403。
const viewTabs = computed(() =>
  props.canEdit ? allViewTabs : allViewTabs.filter((tab) => tab.key !== 'simulate'),
)

const subjectKindOptions: { key: SubjectKind; label: string; desc: string }[] = [
  { key: 'all', label: '全员', desc: '所有能进入的人' },
  { key: 'role', label: '角色', desc: '按岗位批量授权' },
  { key: 'user', label: '用户', desc: '按人精确授权' },
]

const rowPolicyOptions: { key: RowPolicy; label: string; desc: string }[] = [
  { key: 'inherit', label: '继承数据集策略', desc: '走数据集现有的用户例外 / 角色策略 / 默认策略' },
  { key: 'none', label: '不做行级过滤', desc: '高风险：能看到可见表的所有行，仅适合审计/运维' },
  { key: 'custom', label: '自定义条件', desc: '这条授权独立生效，不叠加数据集策略' },
]

const subjectBadgeClass = (kind: SubjectKind) =>
  kind === 'all'
    ? 'bg-slate-100 text-slate-600 border-slate-200'
    : kind === 'role'
      ? 'bg-indigo-50 text-indigo-600 border-indigo-200'
      : 'bg-emerald-50 text-emerald-600 border-emerald-200'
</script>

<template>
  <div class="space-y-5">
    <!-- 顶部 -->
    <div class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 pb-4">
      <div>
        <h2 class="text-lg font-bold text-gray-900 flex items-center gap-2 select-none">
          <svg class="w-5 h-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
          数据集权限
        </h2>
        <p class="text-xs text-gray-400 mt-1 select-none">
          三层控制：能不能进这个数据集 → 能查哪些表 → 能看哪些行。用户级授权覆盖角色级，「全员」是不可收窄的公共基线。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="canEdit"
          class="px-3 py-2 rounded-xl border border-gray-200 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-all"
          @click="view = 'simulate'"
        >
          权限模拟
        </button>
        <button
          v-if="canEdit"
          class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-semibold shadow-md shadow-emerald-500/10 transition-all flex items-center gap-1.5"
          @click="openCreate"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          添加授权
        </button>
      </div>
    </div>

    <!-- 未迁移提示 -->
    <div
      v-if="!scopeSupported"
      class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-2.5 text-xs text-amber-800"
    >
      <span class="mt-0.5">⚠️</span>
      <div>
        <p class="font-semibold">表级 / 行级授权暂未生效</p>
        <p class="mt-0.5 text-amber-700">
          数据库还没有执行权限迁移（<code class="font-mono">db-prod/V205</code>、<code class="font-mono">db-prod-pg/V105</code>）。
          当前所有授权等价于「数据集内全部表」，保存只会写回数据集级授权。
        </p>
      </div>
    </div>

    <!-- 概览 -->
    <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
      <div class="rounded-xl border border-gray-100 bg-gradient-to-br from-white to-slate-50/60 p-3.5">
        <p class="text-[11px] font-semibold text-gray-400 select-none">数据集级 · 谁能进</p>
        <p class="mt-1.5 text-xl font-bold text-gray-900">
          {{ subjectCount }} <span class="text-xs font-medium text-gray-400">条授权</span>
          <span v-if="expiredCount" class="ml-1 text-[11px] font-semibold text-gray-400">（{{ expiredCount }} 条已过期）</span>
        </p>
        <p class="mt-1 text-[11px] text-gray-500">
          角色 {{ roleGrants.length }} · 用户 {{ userGrants.length }} ·
          {{ allMembersGrant ? '含全员基线' : '无全员基线' }}
        </p>
      </div>
      <div class="rounded-xl border border-gray-100 bg-gradient-to-br from-white to-emerald-50/40 p-3.5">
        <p class="text-[11px] font-semibold text-gray-400 select-none">表级 · 能查哪些表</p>
        <p class="mt-1.5 text-xl font-bold text-gray-900">
          {{ tableScopeStats.covered }}
          <span class="text-xs font-medium text-gray-400">/ {{ tables.length }} 张被显式点名</span>
        </p>
        <p class="mt-1 text-[11px] text-gray-500">
          限定范围 {{ tableScopeStats.listCount }} 条 · 全部表 {{ tableScopeStats.allCount }} 条
        </p>
      </div>
      <div class="rounded-xl border border-gray-100 bg-gradient-to-br from-white to-amber-50/40 p-3.5">
        <p class="text-[11px] font-semibold text-gray-400 select-none">行级 · 能看哪些行</p>
        <p class="mt-1.5 text-xl font-bold text-gray-900">
          {{ rowPolicyStats.custom }} <span class="text-xs font-medium text-gray-400">条自定义</span>
        </p>
        <p class="mt-1 text-[11px] text-gray-500">
          继承 {{ rowPolicyStats.inherit }} · 不过滤 {{ rowPolicyStats.none }} ·
          自定义条件 {{ rowPolicyStats.custom }}
        </p>
      </div>
    </div>

    <!-- 新表待确认 -->
    <div
      v-if="hasPendingConfirm"
      class="rounded-xl border border-blue-200 bg-blue-50/60 px-3.5 py-2.5 text-xs text-blue-900"
    >
      <div class="flex items-start gap-2">
        <span class="mt-0.5">🧩</span>
        <div class="flex-1">
          <p class="font-semibold">
            有 {{ pendingTables.length }} 张表没有被任何「指定表」授权覆盖
          </p>
          <p class="mt-0.5 text-blue-700">
            新建表默认跟随「全部表」授权；如果你的数据集里以「指定表」授权为主，
            这些表目前只有被「全部表」条目覆盖时才对成员可见，建议确认是否需要补充授权。
          </p>
          <div class="mt-1.5 flex flex-wrap gap-1">
            <span
              v-for="t in pendingTables.slice(0, 8)"
              :key="t.id"
              class="rounded-md border border-blue-200 bg-white px-1.5 py-0.5 font-mono text-[10px] text-blue-700"
            >{{ t.physical_name }}</span>
            <span v-if="pendingTables.length > 8" class="text-[10px] text-blue-600">+{{ pendingTables.length - 8 }} 张</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 视图切换 -->
    <div class="flex flex-wrap gap-1 border-b border-gray-100">
      <button
        v-for="tab in viewTabs"
        :key="tab.key"
        class="relative -mb-px border-b-2 px-3 py-2 text-xs font-semibold transition-colors"
        :class="view === tab.key
          ? 'border-emerald-500 text-emerald-600'
          : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="view = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <div v-if="loading" class="py-10 text-center text-xs text-gray-400">正在加载授权配置…</div>

    <!-- 视图 1：按主体 -->
    <div v-else-if="view === 'subjects'" class="space-y-4">
      <div
        v-if="!grants.length"
        class="rounded-xl border border-dashed border-gray-200 bg-gray-50/40 py-8 text-center text-xs text-gray-400"
      >
        还没有任何授权。添加授权后，成员才能在这个数据集的 ChatBI 里检索与查询。
      </div>

      <div v-if="allMembersGrant" class="rounded-xl border border-slate-200 bg-slate-50/60 p-3.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="rounded-md border px-1.5 py-0.5 text-[10px] font-semibold" :class="subjectBadgeClass('all')">全员基线</span>
            <span class="text-sm font-semibold text-gray-800">所有能进入该数据集的成员</span>
            <span class="text-[11px] text-gray-500">· {{ scopeText(allMembersGrant) }}</span>
            <span v-if="allMembersGrant.status === 'expired'" class="rounded bg-gray-200 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600">已过期</span>
          </div>
          <div class="flex items-center gap-2 text-[11px] text-gray-500">
            <span>{{ rowPolicyText(allMembersGrant.rowPolicy) }}</span>
            <span>·</span>
            <span>{{ allMembersGrant.expireAt ? `到期 ${String(allMembersGrant.expireAt).slice(0, 10)}` : '长期有效' }}</span>
            <button v-if="canEdit" class="ml-1 font-semibold text-emerald-600 hover:underline" @click="openEdit(allMembersGrant)">编辑</button>
            <button v-if="canEdit" class="font-semibold text-red-500 hover:underline" @click="removeGrant(allMembersGrant)">移除</button>
          </div>
        </div>
        <p class="mt-1.5 text-[11px] text-slate-500">
          全员基线是公共下限：个人/角色授权只能在此基础上追加，不能把这里的表收窄掉。
        </p>
      </div>

      <div v-for="group in [
        { title: '角色授权', items: roleGrants, kind: 'role' as SubjectKind },
        { title: '用户授权', items: userGrants, kind: 'user' as SubjectKind },
      ]" :key="group.title">
        <div class="mb-2 flex items-center gap-2">
          <h3 class="text-xs font-bold text-gray-700">{{ group.title }}</h3>
          <span class="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500">{{ group.items.length }}</span>
        </div>
        <div v-if="group.items.length" class="overflow-hidden rounded-xl border border-gray-100">
          <div
            v-for="grant in group.items"
            :key="grant.key"
            class="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-gray-50 px-3.5 py-2.5 last:border-b-0 hover:bg-gray-50/50"
          >
            <span class="rounded-md border px-1.5 py-0.5 text-[10px] font-semibold" :class="subjectBadgeClass(grant.kind)">
              {{ grant.kind === 'role' ? '角色' : '用户' }}
            </span>
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-gray-800">{{ grant.label }}</p>
              <p v-if="grant.sub" class="truncate font-mono text-[10px] text-gray-400">{{ grant.sub }}</p>
            </div>
            <div class="flex flex-1 flex-wrap items-center justify-end gap-2 text-[11px]">
              <span
                class="rounded-md border px-1.5 py-0.5 font-semibold"
                :class="grant.mode === 'all' ? 'border-gray-200 bg-gray-50 text-gray-600' : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
              >{{ scopeText(grant) }}</span>
              <span v-if="grant.mode === 'list'" class="max-w-[22rem] truncate text-gray-400">
                {{ grant.tables.map(tableName).join('、') }}
              </span>
              <span
                class="rounded-md border px-1.5 py-0.5"
                :class="grant.rowPolicy === 'inherit'
                  ? 'border-gray-200 bg-white text-gray-500'
                  : grant.rowPolicy === 'none'
                    ? 'border-amber-200 bg-amber-50 text-amber-700'
                    : 'border-indigo-200 bg-indigo-50 text-indigo-700'"
              >{{ rowPolicyText(grant.rowPolicy) }}</span>
              <span v-if="grant.rowPolicy === 'custom' && grant.rowCondition" class="max-w-[18rem] truncate font-mono text-[10px] text-gray-400">
                {{ grant.rowCondition }}
              </span>
              <span
                v-if="grant.kind === 'user' && rowFilterEnabled && userExceptionCount(grant.id)"
                class="rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700"
                :title="`该用户有 ${userExceptionCount(grant.id)} 条数据集级行级例外条件`"
              >行级例外 {{ userExceptionCount(grant.id) }}</span>
              <span
                v-else-if="grant.kind === 'role' && rowFilterEnabled && rolePolicyCount(grant.sub)"
                class="rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700"
                :title="`该角色配置了 ${rolePolicyCount(grant.sub)} 条数据集级行级过滤条件`"
              >行级策略 {{ rolePolicyCount(grant.sub) }}</span>
              <span class="text-gray-500">{{ grant.expireAt ? `到期 ${String(grant.expireAt).slice(0, 10)}` : '长期' }}</span>
              <span v-if="grant.status === 'expired'" class="rounded bg-gray-200 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600">已过期</span>
              <template v-if="canEdit">
                <button class="font-semibold text-emerald-600 hover:underline" @click="openEdit(grant)">编辑</button>
                <button class="font-semibold text-red-500 hover:underline" @click="removeGrant(grant)">移除</button>
              </template>
            </div>
          </div>
        </div>
        <p v-else class="rounded-xl border border-dashed border-gray-200 bg-gray-50/30 py-5 text-center text-[11px] text-gray-400">
          暂无{{ group.title }}。
        </p>
      </div>
    </div>

    <!-- 视图 2：按表 -->
    <div v-else-if="view === 'tables'" class="space-y-3">
      <p class="text-[11px] text-gray-400 select-none">
        每张表被哪些授权条目覆盖。「全部表」条目覆盖所有表；「指定表」条目只覆盖被勾选的表。
      </p>
      <div class="overflow-hidden rounded-xl border border-gray-100">
        <div class="grid grid-cols-[minmax(0,1fr)_9rem_minmax(0,1.4fr)] gap-2 border-b border-gray-100 bg-gray-50/60 px-3.5 py-2 text-[10px] font-bold uppercase tracking-wide text-gray-400">
          <span>表</span>
          <span>覆盖条目</span>
          <span>授予范围</span>
        </div>
        <div
          v-for="t in tables"
          :key="t.id"
          class="grid grid-cols-[minmax(0,1fr)_9rem_minmax(0,1.4fr)] items-start gap-2 border-b border-gray-50 px-3.5 py-2.5 last:border-b-0 hover:bg-gray-50/40"
        >
          <div class="min-w-0">
            <p class="truncate font-mono text-xs font-semibold text-gray-800">{{ t.physical_name }}</p>
            <p v-if="t.term" class="truncate text-[10px] text-gray-400">{{ t.term }}</p>
          </div>
          <div>
            <span
              class="rounded-md border px-1.5 py-0.5 text-[10px] font-semibold"
              :class="grants.filter((g) => g.mode === 'all' || g.tables.includes(t.id)).length
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-gray-200 bg-gray-50 text-gray-500'"
            >
              {{ grants.filter((g) => g.mode === 'all' || g.tables.includes(t.id)).length }} 条
            </span>
          </div>
          <div class="flex flex-wrap gap-1">
            <span
              v-for="g in grants.filter((x) => x.mode === 'all' || x.tables.includes(t.id)).slice(0, 6)"
              :key="g.key"
              class="rounded border px-1.5 py-0.5 text-[10px] font-medium"
              :class="subjectBadgeClass(g.kind)"
            >{{ g.label }}</span>
            <span v-if="!grants.filter((x) => x.mode === 'all' || x.tables.includes(t.id)).length" class="text-[10px] italic text-gray-400">
              仅被「全部表」以外的条目排除，当前无人通过表级白名单获得它
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 视图 3：行级策略 -->
    <div v-else-if="view === 'rows'" class="space-y-3">
      <div class="rounded-xl border border-gray-100 bg-white p-3.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-xs font-bold text-gray-700">
            数据集自身行级策略
            <span class="ml-1 rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500">只读</span>
          </h3>
          <span
            class="inline-flex items-center gap-1.5 rounded-lg border px-2 py-0.5 text-[10px] font-semibold"
            :class="rowFilterEnabled ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
          >
            <span class="h-1.5 w-1.5 rounded-full" :class="rowFilterEnabled ? 'bg-amber-500' : 'bg-gray-300'"></span>
            {{ rowFilterEnabled ? `已启用校验 · ${datasetRowRuleCount} 条规则` : '未启用精细化权限校验' }}
          </span>
        </div>
        <p class="mt-1 text-[11px] text-gray-400">
          选「继承数据集策略」的授权会走这里的规则：用户例外 → 角色策略 → 默认规则。
          编辑入口在「元数据管理 → 数据权限配置」，这里只读展示，避免和「按人覆盖」混淆。
        </p>
        <div v-if="!rowFilterEnabled" class="mt-2 rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-1.5 text-[11px] text-gray-500">
          未启用精细化权限校验：继承策略的授权可以看到所授权表的<b>全部数据行</b>。
        </div>
        <div v-else class="mt-2.5 grid grid-cols-1 gap-2 md:grid-cols-3">
          <div class="rounded-lg border border-amber-100 bg-amber-50/40 p-2.5">
            <p class="text-[10px] font-bold text-amber-700">用户例外 <span class="font-mono">{{ datasetUserExceptions.length }}</span></p>
            <div v-if="datasetUserExceptions.length" class="mt-1.5 space-y-1.5">
              <div v-for="group in datasetUserExceptions" :key="group.key">
                <p class="text-[10px] font-semibold text-gray-600">{{ group.label }}</p>
                <code v-for="(cond, idx) in group.conditions" :key="idx" class="mt-0.5 block break-all font-mono text-[10px] text-gray-500">{{ cond }}</code>
              </div>
            </div>
            <p v-else class="mt-1.5 text-[10px] italic text-gray-400">无用户例外</p>
          </div>
          <div class="rounded-lg border border-emerald-100 bg-emerald-50/40 p-2.5">
            <p class="text-[10px] font-bold text-emerald-700">角色策略 <span class="font-mono">{{ datasetRolePolicies.length }}</span></p>
            <div v-if="datasetRolePolicies.length" class="mt-1.5 space-y-1.5">
              <div v-for="group in datasetRolePolicies" :key="group.code">
                <p class="text-[10px] font-semibold text-gray-600">{{ group.label }}</p>
                <code v-for="(cond, idx) in group.conditions" :key="idx" class="mt-0.5 block break-all font-mono text-[10px] text-gray-500">{{ cond }}</code>
              </div>
            </div>
            <p v-else class="mt-1.5 text-[10px] italic text-gray-400">无角色策略</p>
          </div>
          <div class="rounded-lg border border-slate-200 bg-slate-50/40 p-2.5">
            <p class="text-[10px] font-bold text-slate-600">默认规则 <span class="font-mono">{{ datasetDefaultRules.length }}</span></p>
            <code v-for="(cond, idx) in datasetDefaultRules" :key="idx" class="mt-1 block break-all font-mono text-[10px] text-gray-500">{{ cond }}</code>
            <p v-if="!datasetDefaultRules.length" class="mt-1.5 text-[10px] italic text-gray-400">无默认规则</p>
            <p class="mt-1.5 text-[10px] text-gray-400">用户例外、角色策略都没命中时生效。</p>
          </div>
        </div>
      </div>

      <div class="rounded-xl border border-gray-100 bg-white p-3.5">
        <h3 class="text-xs font-bold text-gray-700">三种行级策略</h3>
        <ul class="mt-2 space-y-1.5 text-[11px] text-gray-600">
          <li><span class="font-semibold text-gray-700">继承数据集策略</span>（默认）：走数据集自身的行级配置（用户例外 → 角色策略 → 默认策略），在「数据权限配置」里维护，改动对所有继承的人生效。</li>
          <li><span class="font-semibold text-amber-700">不做行级过滤</span>：跳过重写。只应给审计、运维这类需要看全量的个人授权，属于高风险选项。</li>
          <li><span class="font-semibold text-indigo-700">自定义条件</span>：这条授权独立生效，不再叠加数据集策略。条件语法与行级配置一致，例如 <code class="font-mono">fact_task.dept_code = 'FIN'</code>。</li>
        </ul>
      </div>

      <div class="overflow-hidden rounded-xl border border-gray-100">
        <div class="grid grid-cols-[minmax(0,1fr)_8rem_minmax(0,1.6fr)_7rem] gap-2 border-b border-gray-100 bg-gray-50/60 px-3.5 py-2 text-[10px] font-bold uppercase tracking-wide text-gray-400">
          <span>主体</span>
          <span>策略</span>
          <span>条件</span>
          <span>有效期</span>
        </div>
        <div
          v-for="g in grants.filter((x) => x.rowPolicy !== 'inherit')"
          :key="g.key"
          class="grid grid-cols-[minmax(0,1fr)_8rem_minmax(0,1.6fr)_7rem] items-center gap-2 border-b border-gray-50 px-3.5 py-2.5 last:border-b-0"
        >
          <div class="flex min-w-0 items-center gap-1.5">
            <span class="rounded border px-1.5 py-0.5 text-[10px] font-semibold" :class="subjectBadgeClass(g.kind)">{{ g.label }}</span>
            <span class="truncate text-[10px] text-gray-400">{{ scopeText(g) }}</span>
          </div>
          <span
            class="w-fit rounded-md border px-1.5 py-0.5 text-[10px] font-semibold"
            :class="g.rowPolicy === 'none' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-indigo-200 bg-indigo-50 text-indigo-700'"
          >{{ rowPolicyText(g.rowPolicy) }}</span>
          <code v-if="g.rowPolicy === 'custom'" class="truncate font-mono text-[10px] text-gray-600">{{ g.rowCondition }}</code>
          <span v-else class="text-[10px] italic text-gray-400">不附加 WHERE 条件</span>
          <span class="text-[10px] text-gray-500">{{ g.expireAt ? String(g.expireAt).slice(0, 10) : '长期' }}</span>
        </div>
        <div v-if="!grants.filter((x) => x.rowPolicy !== 'inherit').length" class="px-3.5 py-6 text-center text-[11px] text-gray-400">
          所有授权都继承数据集策略，没有单独的行级覆盖。
        </div>
      </div>
    </div>

    <!-- 视图 4：权限模拟 -->
    <div v-else class="space-y-3">
      <div class="flex flex-wrap items-end gap-2 rounded-xl border border-gray-100 bg-gray-50/50 p-3">
        <div class="min-w-[16rem] flex-1">
          <label class="mb-1 block text-[11px] font-semibold text-gray-500">选择成员</label>
          <select v-model.number="simUserId" class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs">
            <option :value="null">请选择要模拟的成员</option>
            <option v-for="u in candidates.users" :key="u.id" :value="Number(u.id)">
              {{ u.real_name || u.user_name }}（{{ u.user_name }}）
            </option>
          </select>
        </div>
        <button
          class="rounded-xl bg-gray-900 px-4 py-2 text-xs font-semibold text-white transition-all hover:bg-gray-800 disabled:opacity-40"
          :disabled="!simUserId || simLoading"
          @click="runSimulate"
        >{{ simLoading ? '计算中…' : '开始模拟' }}</button>
        <p class="text-[11px] text-gray-400">模拟结果由后端按真实判定链计算，与 ChatBI 执行时一致。</p>
      </div>

      <div v-if="!simResult" class="rounded-xl border border-dashed border-gray-200 bg-gray-50/30 py-8 text-center text-[11px] text-gray-400">
        选一个成员，看他在这个数据集里最终能看到哪些表、哪些行，以及命中了哪些授权。
      </div>

      <template v-else>
        <div class="rounded-xl border p-3.5" :class="simResult.has_access ? 'border-emerald-200 bg-emerald-50/40' : 'border-red-200 bg-red-50/40'">
          <p class="text-xs font-bold" :class="simResult.has_access ? 'text-emerald-800' : 'text-red-800'">
            {{ simResult.has_access ? '① 数据集级：可以进入' : '① 数据集级：被拒绝' }}
          </p>
          <p class="mt-1 text-[11px]" :class="simResult.has_access ? 'text-emerald-700' : 'text-red-700'">
            <template v-if="simResult.has_access">
              命中授权来源：{{ (simResult.scope?.sources || []).map((s: string) => s === 'user' ? '用户级' : s === 'role' ? '角色级' : '全员基线').join(' + ') || '—' }}
            </template>
            <template v-else>该成员没有任何命中该数据集的授权，ChatBI 不会返回这个数据集的任何内容。</template>
          </p>
        </div>

        <div class="rounded-xl border border-gray-100 p-3.5">
          <p class="text-xs font-bold text-gray-800">
            ② 表级：{{ simVisibleTables.length }} / {{ simResult.total_tables }} 张表可见
            <span v-if="simResult.scope?.mode === 'all'" class="ml-1 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600">范围=全部表</span>
            <span v-else class="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">范围=指定表</span>
          </p>
          <div class="mt-2 flex flex-wrap gap-1">
            <span
              v-for="t in simVisibleTables"
              :key="t.id"
              class="rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] text-emerald-700"
            >{{ t.physical_name }}</span>
          </div>
          <div v-if="simExcludedTables.length" class="mt-2.5">
            <p class="text-[10px] font-semibold text-gray-400">被表级白名单排除（查询会返回 Permission Denied）</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="t in simExcludedTables"
                :key="t.id"
                class="rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[10px] text-gray-500 line-through"
              >{{ t.physical_name }}</span>
            </div>
          </div>
        </div>

        <div class="rounded-xl border border-gray-100 p-3.5">
          <p class="text-xs font-bold text-gray-800">③ 行级：{{ rowPolicyText(simResult.row_policy) }}</p>
          <p v-if="simResult.row_policy === 'custom' && simResult.row_condition" class="mt-1 font-mono text-[11px] text-indigo-700">
            {{ simResult.row_condition }}
          </p>
          <p v-else-if="simResult.row_policy === 'none'" class="mt-1 text-[11px] text-amber-700">
            该成员的授权显式跳过了行级过滤，能看到可见表的所有行。
          </p>
          <p v-else class="mt-1 text-[11px] text-gray-500">
            按数据集自身行级配置重写 WHERE（用户例外 → 角色策略 → 默认策略）。
          </p>
        </div>

        <div class="rounded-xl border border-gray-100 p-3.5">
          <p class="text-xs font-bold text-gray-800">命中的授权条目</p>
          <div v-if="simGrantRows.length" class="mt-2 space-y-1.5">
            <div
              v-for="g in simGrantRows"
              :key="g.permission_id"
              class="flex flex-wrap items-center gap-2 rounded-lg border border-gray-100 bg-gray-50/50 px-2.5 py-1.5 text-[11px]"
            >
              <span class="rounded border px-1.5 py-0.5 text-[10px] font-semibold" :class="subjectBadgeClass(g.subject)">
                {{ g.subject === 'all' ? '全员基线' : g.subject === 'role' ? '角色' : '用户' }}
              </span>
              <span class="font-semibold text-gray-700">{{ g.subject === 'role' ? (g.subject_name || '角色') : g.subject === 'all' ? '所有成员' : '本人' }}</span>
              <span class="text-gray-500">{{ g.mode === 'all' ? '全部表' : `${g.table_ids.length} 张表` }}</span>
              <span class="text-gray-500">{{ rowPolicyText(g.row_policy) }}</span>
              <code v-if="g.row_condition" class="font-mono text-[10px] text-gray-500">{{ g.row_condition }}</code>
            </div>
          </div>
          <p v-else class="mt-1 text-[11px] text-gray-400">没有命中任何授权行。</p>
          <p v-if="simResult.roles?.length" class="mt-2 text-[10px] text-gray-400">
            该成员的角色：{{ simResult.roles.map((r: any) => r.name || r.code).join('、') }}
          </p>
        </div>
      </template>
    </div>

    <!-- 抽屉：新增 / 编辑 -->
    <Teleport to="body">
      <div
        v-if="drawerOpen"
        class="fixed inset-0 z-[100] flex justify-end bg-black/40 backdrop-blur-sm"
        @click.self="closeDrawer"
      >
        <div class="flex h-full w-full max-w-xl flex-col border-l border-gray-100 bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-gray-100 bg-gray-50/60 px-5 py-3.5">
            <div>
              <h3 class="text-sm font-bold text-gray-800">{{ editingKey ? '编辑授权' : '添加授权' }}</h3>
              <p class="mt-0.5 text-[11px] text-gray-400">{{ draftSummary }}</p>
            </div>
            <button class="text-gray-400 transition-all hover:text-gray-600" @click="closeDrawer">
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>

          <!-- 步骤条 -->
          <div class="flex items-center gap-1 border-b border-gray-100 px-5 py-2.5">
            <div
              v-for="(label, idx) in ['主体', '表范围', '行级', '有效期']"
              :key="label"
              class="flex items-center gap-1.5"
            >
              <button
                class="flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold transition-colors"
                :class="drawerStep === idx
                  ? 'bg-emerald-600 text-white'
                  : drawerStep > idx
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-gray-100 text-gray-400'"
                @click="drawerStep = idx"
              >{{ idx + 1 }}</button>
              <span class="text-[11px]" :class="drawerStep === idx ? 'font-semibold text-gray-800' : 'text-gray-400'">{{ label }}</span>
              <span v-if="idx < 3" class="mx-0.5 text-gray-300">›</span>
            </div>
          </div>

          <div class="flex-1 overflow-y-auto px-5 py-4">
            <!-- Step 1 主体 -->
            <div v-if="drawerStep === 0" class="space-y-3">
              <div class="grid grid-cols-3 gap-2">
                <button
                  v-for="opt in subjectKindOptions"
                  :key="opt.key"
                  class="rounded-xl border px-2.5 py-2 text-left transition-all"
                  :class="draft.kind === opt.key ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                  :disabled="!!editingKey && draft.kind !== opt.key"
                  @click="draft.kind = opt.key"
                >
                  <p class="text-xs font-semibold text-gray-800">{{ opt.label }}</p>
                  <p class="mt-0.5 text-[10px] text-gray-400">{{ opt.desc }}</p>
                </button>
              </div>

              <input
                v-if="draft.kind !== 'all'"
                v-model="subjectSearch"
                type="text"
                placeholder="搜索名称…"
                class="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs"
              />

              <div v-if="draft.kind === 'role'" class="max-h-64 space-y-1 overflow-y-auto rounded-xl border border-gray-100 p-2">
                <label
                  v-for="r in filteredRoles"
                  :key="r.id"
                  class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    class="h-3.5 w-3.5 rounded border-gray-300 text-emerald-600"
                    :checked="draft.roleIds.includes(Number(r.id))"
                    @change="toggleDraftSubject('role', Number(r.id))"
                  />
                  <span class="text-xs font-medium text-gray-700">{{ r.name }}</span>
                  <span class="font-mono text-[10px] text-gray-400">{{ r.code }}</span>
                </label>
                <p v-if="!filteredRoles.length" class="py-3 text-center text-[11px] text-gray-400">没有可选角色</p>
              </div>

              <div v-else-if="draft.kind === 'user'" class="max-h-64 space-y-1 overflow-y-auto rounded-xl border border-gray-100 p-2">
                <label
                  v-for="u in filteredUsers"
                  :key="u.id"
                  class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    class="h-3.5 w-3.5 rounded border-gray-300 text-emerald-600"
                    :checked="draft.userIds.includes(Number(u.id))"
                    @change="toggleDraftSubject('user', Number(u.id))"
                  />
                  <span class="text-xs font-medium text-gray-700">{{ u.real_name || u.user_name }}</span>
                  <span class="font-mono text-[10px] text-gray-400">{{ u.user_name }}</span>
                </label>
                <p v-if="!filteredUsers.length" class="py-3 text-center text-[11px] text-gray-400">没有可选用户</p>
              </div>

              <p v-else class="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2 text-[11px] text-slate-600">
                全员授权对所有能进入该数据集的成员生效，是公共下限；个人/角色授权只能在它之上追加。
              </p>
            </div>

            <!-- Step 2 表范围 -->
            <div v-else-if="drawerStep === 1" class="space-y-3">
              <div class="grid grid-cols-2 gap-2">
                <button
                  class="rounded-xl border px-3 py-2 text-left transition-all"
                  :class="draft.scopeMode === 'all' ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                  @click="draft.scopeMode = 'all'"
                >
                  <p class="text-xs font-semibold text-gray-800">全部表</p>
                  <p class="mt-0.5 text-[10px] text-gray-400">含以后新增的表（推荐）</p>
                </button>
                <button
                  class="rounded-xl border px-3 py-2 text-left transition-all"
                  :class="draft.scopeMode === 'list' ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                  @click="draft.scopeMode = 'list'"
                >
                  <p class="text-xs font-semibold text-gray-800">指定表</p>
                  <p class="mt-0.5 text-[10px] text-gray-400">白名单，新增表不会自动加入</p>
                </button>
              </div>

              <template v-if="draft.scopeMode === 'list'">
                <div class="flex items-center gap-2">
                  <input
                    v-model="tableSearch"
                    type="text"
                    placeholder="搜索表名或业务术语…"
                    class="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-xs"
                  />
                  <button class="rounded-lg border border-gray-200 px-2 py-1.5 text-[11px] font-semibold text-gray-600 hover:bg-gray-50" @click="selectAllTables">全选</button>
                  <button class="rounded-lg border border-gray-200 px-2 py-1.5 text-[11px] font-semibold text-gray-600 hover:bg-gray-50" @click="clearTables">清空</button>
                </div>
                <p class="text-[10px] text-gray-400">已选 {{ draft.tableIds.length }} / {{ tables.length }} 张</p>
                <div class="max-h-72 space-y-0.5 overflow-y-auto rounded-xl border border-gray-100 p-2">
                  <label
                    v-for="t in filteredTables"
                    :key="t.id"
                    class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      class="h-3.5 w-3.5 rounded border-gray-300 text-emerald-600"
                      :checked="draft.tableIds.includes(t.id)"
                      @change="toggleDraftTable(t.id)"
                    />
                    <span class="font-mono text-xs text-gray-700">{{ t.physical_name }}</span>
                    <span v-if="t.term" class="truncate text-[10px] text-gray-400">{{ t.term }}</span>
                  </label>
                  <p v-if="!filteredTables.length" class="py-3 text-center text-[11px] text-gray-400">没有匹配的表</p>
                </div>
                <p class="rounded-lg border border-blue-200 bg-blue-50/60 px-2.5 py-1.5 text-[10px] text-blue-800">
                  白名单是 fail-closed 的：以后新加的表默认不在这条授权里，需要回来补勾。
                </p>
              </template>
            </div>

            <!-- Step 3 行级 -->
            <div v-else-if="drawerStep === 2" class="space-y-2">
              <button
                v-for="opt in rowPolicyOptions"
                :key="opt.key"
                class="w-full rounded-xl border px-3 py-2 text-left transition-all"
                :class="draft.rowPolicy === opt.key ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                @click="draft.rowPolicy = opt.key"
              >
                <p class="text-xs font-semibold text-gray-800">{{ opt.label }}</p>
                <p class="mt-0.5 text-[10px] text-gray-400">{{ opt.desc }}</p>
              </button>

              <div v-if="draft.rowPolicy === 'custom'" class="space-y-1.5 pt-1">
                <label class="block text-[11px] font-semibold text-gray-500">行级条件</label>
                <input
                  v-model="draft.rowCondition"
                  type="text"
                  placeholder="例如 fact_task.dept_code = 'FIN'"
                  class="w-full rounded-xl border border-gray-200 px-3 py-2 font-mono text-xs"
                />
                <p class="text-[10px] text-gray-400">
                  支持 <code class="font-mono">= != IN LIKE &gt; &lt;</code>，字段用 <code class="font-mono">表.字段</code> 形式；不能包含分号或注释。
                </p>
              </div>
              <p v-if="draft.rowPolicy === 'none'" class="rounded-lg border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-[10px] text-amber-800">
                选择「不做行级过滤」后，该主体查可见表时不会附加任何数据权限条件。
              </p>
            </div>

            <!-- Step 4 有效期 -->
            <div v-else class="space-y-2">
              <button
                class="w-full rounded-xl border px-3 py-2 text-left transition-all"
                :class="draft.expiryMode === 'never' ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                @click="draft.expiryMode = 'never'"
              >
                <p class="text-xs font-semibold text-gray-800">长期有效</p>
                <p class="mt-0.5 text-[10px] text-gray-400">不会自动失效，需要手动移除</p>
              </button>
              <button
                class="w-full rounded-xl border px-3 py-2 text-left transition-all"
                :class="draft.expiryMode === 'date' ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'"
                @click="draft.expiryMode = 'date'"
              >
                <p class="text-xs font-semibold text-gray-800">到期时间</p>
                <p class="mt-0.5 text-[10px] text-gray-400">到期后自动失效，授权行保留便于续期</p>
              </button>
              <input
                v-if="draft.expiryMode === 'date'"
                v-model="draft.expireAt"
                type="date"
                class="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs"
              />
            </div>
          </div>

          <div class="flex items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/40 px-5 py-3">
            <button
              class="rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 transition-all hover:bg-white disabled:opacity-40"
              :disabled="drawerStep === 0"
              @click="drawerStep = Math.max(0, drawerStep - 1)"
            >上一步</button>
            <div class="flex items-center gap-2">
              <button class="rounded-xl px-3 py-2 text-xs font-semibold text-gray-500 hover:text-gray-700" @click="closeDrawer">取消</button>
              <button
                v-if="drawerStep < 3"
                class="rounded-xl bg-gray-900 px-4 py-2 text-xs font-semibold text-white transition-all hover:bg-gray-800 disabled:opacity-40"
                :disabled="!canNext"
                @click="drawerStep += 1"
              >下一步</button>
              <button
                v-else
                class="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-md shadow-emerald-500/10 transition-all hover:bg-emerald-700 disabled:opacity-50"
                :disabled="!canNext || saving"
                @click="submitDrawer"
              >{{ saving ? '保存中…' : editingKey ? '保存修改' : '确认授权' }}</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
