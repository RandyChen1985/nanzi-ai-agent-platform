<script setup lang="ts">
import ConfirmModal from '@/components/ConfirmModal.vue'
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
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import axios from '../../utils/axios'
import { useToast } from '../../composables/useToast'

type ScopeMode = 'all' | 'list'
type RowPolicy = 'inherit' | 'none' | 'custom'
type SubjectKind = 'all' | 'role' | 'user'

interface TableMeta {
  id: number
  physical_name: string
  term?: string | null
  /** 该表的列清单（后端 /permissions 一并返回，字段级配置用） */
  columns?: string[]
}

type ColumnMode = 'include' | 'exclude'

/** 单张表的字段级约束：include = 只保留名单内；exclude = 剔除名单内 */
interface ColumnRule {
  mode: ColumnMode
  names: string[]
}

interface Grant {
  key: string
  kind: SubjectKind
  id?: number
  label: string
  sub?: string
  mode: ScopeMode
  tables: number[]
  columns: Record<number, ColumnRule>
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
  /** 从外部直达某个子视图（数据行策略），值来自路由的 permView */
  initialView?: 'subjects' | 'tables' | 'columns' | 'rows'
}>()

const emit = defineEmits<{
  (e: 'changed'): void
  /** 去元数据管理编辑该数据集的行级策略：详情页只读，编辑入口在列表页的弹窗里 */
  (e: 'edit-row-policy'): void
}>()

const { showToast } = useToast()

const loading = ref(true)
const saving = ref(false)
const scopeSupported = ref(true)
const grants = ref<Grant[]>([])
const tables = ref<TableMeta[]>([])
const candidates = ref<{ roles: any[]; users: any[] }>({ roles: [], users: [] })

type ViewKey = 'subjects' | 'tables' | 'columns' | 'rows'
const resolveInitialView = (): ViewKey =>
  props.initialView === 'tables' || props.initialView === 'columns' || props.initialView === 'rows'
    ? props.initialView
    : 'subjects'
const view = ref<ViewKey>(resolveInitialView())

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
      columns: Object.fromEntries(
        Object.entries(raw?.scope?.columns || {}).map(([tid, rule]: [string, any]) => [
          Number(tid),
          {
            mode: rule?.mode === 'exclude' ? 'exclude' : 'include',
            names: Array.isArray(rule?.names) ? rule.names.map((n: any) => String(n)) : [],
          },
        ]),
      ) as Record<number, ColumnRule>,
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

onMounted(() => {
  document.addEventListener('click', closeSimPicker)
  document.addEventListener('keydown', handleSimEsc)
  void load()
})
onUnmounted(() => {
  document.removeEventListener('click', closeSimPicker)
  document.removeEventListener('keydown', handleSimEsc)
})

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
  columns: {} as Record<number, ColumnRule>,
  rowPolicy: 'inherit' as RowPolicy,
  rowCondition: '',
  expiryMode: 'never' as 'never' | 'date',
  expireAt: '',
})

// ---------------------------------------------------------------------------
// 字段级（列级）权限
// ---------------------------------------------------------------------------
const colDetailTableId = ref<number | null>(null)
const colMode = ref<ColumnMode>('include')

/** 抽屉里「当前生效的表」：全部表 = 数据集全部；指定表 = 勾选的那些 */
const draftActiveTables = computed<TableMeta[]>(() =>
  draft.scopeMode === 'all'
    ? tables.value
    : tables.value.filter((t) => draft.tableIds.includes(t.id)),
)

const colRuleOf = (tableId: number) => draft.columns[tableId] || null

const colModeText = (tableId: number) => {
  const rule = colRuleOf(tableId)
  if (!rule) return '全部字段'
  return rule.mode === 'include' ? `限定 ${rule.names.length} 个` : `排除 ${rule.names.length} 个`
}

const colDetailTable = computed(
  () => tables.value.find((t) => t.id === colDetailTableId.value) || null,
)

/** 打开某张表的字段面板：沿用已有规则的模式，新配置默认「限定」（新列 fail-closed） */
const openColDetail = (tableId: number) => {
  colDetailTableId.value = tableId
  colMode.value = draft.columns[tableId]?.mode || 'include'
}

const setColMode = (mode: ColumnMode) => {
  if (colDetailTableId.value === null) return
  colMode.value = mode
  const rule = draft.columns[colDetailTableId.value]
  if (rule) draft.columns[colDetailTableId.value] = { mode, names: [...rule.names] }
}

const toggleCol = (name: string) => {
  const tableId = colDetailTableId.value
  if (tableId === null) return
  const rule = draft.columns[tableId]
  const names = rule ? [...rule.names] : []
  const idx = names.indexOf(name)
  if (idx >= 0) names.splice(idx, 1)
  else names.push(name)
  if (!names.length) delete draft.columns[tableId]
  else draft.columns[tableId] = { mode: rule?.mode || colMode.value, names }
}

const clearColRule = (tableId: number) => {
  delete draft.columns[tableId]
  if (colDetailTableId.value === tableId) colDetailTableId.value = null
}

/** 切换表范围后，把不在范围内的字段配置一并丢掉，避免存下悬空规则 */
const pruneColRules = () => {
  const alive = new Set(draftActiveTables.value.map((t) => t.id))
  Object.keys(draft.columns).forEach((key) => {
    if (!alive.has(Number(key))) delete draft.columns[key]
  })
  if (colDetailTableId.value !== null && !alive.has(colDetailTableId.value)) {
    colDetailTableId.value = null
  }
}

// 表范围一变，字段配置的可选表就变了：立刻清掉不在范围内的，避免存下悬空规则
watch(
  () => [draft.scopeMode, draft.tableIds.join(',')],
  () => pruneColRules(),
)

const resetDraft = () => {
  drawerStep.value = 0
  editingKey.value = null
  subjectSearch.value = ''
  tableSearch.value = ''
  colDetailTableId.value = null
  draft.columns = {}
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
  draft.columns = JSON.parse(JSON.stringify(grant.columns || {}))
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
  // 字段范围是纯可选项：不配就是全部字段，永远可以「下一步」
  if (drawerStep.value === 2) return true
  if (drawerStep.value === 3) {
    if (draft.rowPolicy !== 'custom') return true
    return draft.rowCondition.trim().length > 0
  }
  if (drawerStep.value === 4) return draft.expiryMode === 'never' || !!draft.expireAt
  return true
})

// 「到期 = 这条授权消失」，判定时整行被跳过（dataset_scope_service 的 _is_expired）。
// 于是到期后真正生效的，是这个用户在**没有这条授权**时的范围 —— 如果那更宽，
// 管理员设「到期时间」以为在收回权限，实际是在给自己挖坑。
// 这里在配置阶段就把这件事说出来，不阻断保存，只让结果可预期。
const expiryFallback = computed(() => {
  if (draft.expiryMode !== 'date' || !draft.expireAt) return null
  // 角色/全员本来就是这个数据集最宽的层，只有「按人收窄」才会出现回落变宽的错觉。
  if (!draft.userIds.length) return null

  const liveBase = allMembersGrant.value?.status === 'expired' ? null : allMembersGrant.value
  const roles = roleGrants.value.filter((g) => g.status !== 'expired')
  if (!liveBase && !roles.length) return null

  // 全员基线对**所有人**生效 → 回落目标确定；角色授权要求该用户属于那个角色 →
  // 前端不知道成员关系，只能提示「可能」。
  let certain: string | null = null
  if (liveBase && draft.scopeMode === 'list') {
    if (liveBase.mode === 'all') certain = '全员授权（全部表）'
    else if (liveBase.tables.length > draft.tableIds.length) {
      certain = `全员授权（${liveBase.tables.length} 张表）`
    }
  }

  const fallbackPolicy = liveBase?.rowPolicy ?? roles[0]?.rowPolicy ?? 'inherit'
  // 表范围变宽、行级条件失效，是两类独立风险，任一命中都要提示。
  const tableWider = !!certain
  const rowWider = draft.rowPolicy === 'custom' && fallbackPolicy !== 'custom'
  if (!tableWider && !rowWider) return null

  return {
    certain,
    definite: !!liveBase,
    fallback: liveBase ? '全员授权' : '角色授权',
    tableCount: draft.tableIds.length,
    tableWider,
    rowWider,
  }
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
  const colCount = Object.keys(draft.columns || {}).length
  const colText = colCount ? ` · ${colCount} 张表收窄字段` : ''
  return `${draftSubjects.value.length} 个主体 · ${scopeText}${colText} · ${policyText} · ${expiryText}`
})

const submitDrawer = async () => {
  if (!canNext.value) return
  saving.value = true
  try {
    // 字段级：只提交「表范围里真正生效」的规则；空对象不传，后端按「全部字段」处理
    const aliveTables = new Set(draftActiveTables.value.map((t) => t.id))
    const columns: Record<number, ColumnRule> = {}
    Object.entries(draft.columns).forEach(([tid, rule]) => {
      const tableId = Number(tid)
      if (!aliveTables.has(tableId) || !rule.names.length) return
      columns[tableId] = { mode: rule.mode, names: [...rule.names] }
    })
    const base = {
      scope: {
        mode: draft.scopeMode,
        tables: draft.scopeMode === 'list' ? [...draft.tableIds] : [],
        columns,
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

const pendingRemoval = ref<Grant | null>(null)

const removeGrant = (grant: Grant) => {
  pendingRemoval.value = grant
}

const cancelRemoval = () => {
  pendingRemoval.value = null
}

const confirmRemoval = async () => {
  const grant = pendingRemoval.value
  pendingRemoval.value = null
  if (!grant) return
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
// 成员可能有几百个，原生 select 只能盲滚：改成可搜索的下拉（输入过滤姓名/账号）。
const simOpen = ref(false)
const simQuery = ref('')
const simSearchRef = ref<HTMLInputElement | null>(null)
const simSelectedLabel = computed(() => {
  const u = (candidates.value.users || []).find((item) => Number(item.id) === simUserId.value)
  if (!u) return ''
  const name = String(u.real_name || '').trim()
  const account = String(u.user_name || '').trim()
  return name && name !== account ? `${name}（${account}）` : account || name
})
const simFilteredUsers = computed(() => {
  const keyword = simQuery.value.trim().toLowerCase()
  const users = candidates.value.users || []
  if (!keyword) return users
  return users.filter((u) =>
    `${u.real_name || ''} ${u.user_name || ''}`.toLowerCase().includes(keyword),
  )
})
const toggleSimOpen = () => {
  simOpen.value = !simOpen.value
  if (simOpen.value) simQuery.value = ''
}
const pickSimUser = (user: any) => {
  simUserId.value = Number(user.id)
  simOpen.value = false
  simQuery.value = ''
  // 换了人必须清掉上一次结果，否则屏幕上还是上一个人的表和行。
  simResult.value = null
}
const closeSimPicker = () => {
  simOpen.value = false
}
// 模拟是「查一个具体的人」的一次性动作，不占页签：放进弹框，看完就关。
const simModalOpen = ref(false)
const openSimulateModal = () => {
  simModalOpen.value = true
}
const closeSimModal = () => {
  simModalOpen.value = false
  simOpen.value = false
}
const handleSimEsc = (event: KeyboardEvent) => {
  if (event.key !== 'Escape') return
  if (scopeHelpKey.value) {
    scopeHelpKey.value = null
    return
  }
  if (simModalOpen.value) closeSimModal()
}
watch(simOpen, async (open) => {
  if (!open) return
  await nextTick()
  simSearchRef.value?.focus()
})
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

/** 数据集自身策略：用户例外 / 角色策略 / 默认规则（只读，编辑入口在「数据行策略」） */
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

// ---- 字段范围视图 ----
/** 该授权条目在这张表上能看到多少列；null = 表本身不可见 */
const visibleColCount = (grant: Grant, table: TableMeta): number | null => {
  if (grant.mode === 'list' && !grant.tables.includes(table.id)) return null
  const total = (table.columns || []).length
  const rule = grant.columns[table.id]
  if (!rule) return total
  return rule.mode === 'include'
    ? Math.min(rule.names.length, total)
    : Math.max(0, total - rule.names.length)
}

const colNarrowedTables = computed(() =>
  tables.value.filter((t) =>
    grants.value.some((g) => {
      if (g.status === 'expired') return false
      const seen = visibleColCount(g, t)
      return seen !== null && seen < (t.columns || []).length
    }),
  ),
)

const onlyColNarrowed = ref(false)

const columnMatrixRows = computed(() =>
  (onlyColNarrowed.value ? colNarrowedTables.value : tables.value).map((t) => {
    const total = (t.columns || []).length
    const cells = grants.value.map((g) => ({
      grant: g,
      visible: g.status === 'expired' ? null : visibleColCount(g, t),
      total,
    }))
    return { table: t, cells, narrowedBy: cells.filter((c) => c.visible !== null && c.visible < total).length }
  }),
)

const allViewTabs = [
  { key: 'subjects', label: '按主体' },
  { key: 'tables', label: '按表' },
  { key: 'columns', label: '字段范围' },
  { key: 'rows', label: '数据行策略' },
] as const

type ScopeHelpKey = 'dataset' | 'table' | 'row'

const scopeHelpKey = ref<ScopeHelpKey | null>(null)

// 三层门禁的说明：一张卡片上的数字看不出来「这层到底管什么」，
// 而配错层的代价不一样（数据集级配宽 = 全表可见），所以每层都给一个「?」。
const SCOPE_HELP: Record<ScopeHelpKey, {
  title: string
  subtitle: string
  impact: string[]
  principle: string[]
  pitfall: string
}> = {
  dataset: {
    title: '数据集级 · 谁能进',
    subtitle: '决定「这个数据集对谁存在」',
    impact: [
      '在 ChatBI 里能不能检索到这个数据集的表结构：没有授权，模型连表名和字段都看不到。',
      '能不能对它下面的表执行 SQL：不在授权范围内的数据集，查询会直接返回 Permission Denied。',
      '在数据集列表、资源广场里能不能看到它、能不能申请它的权限。',
    ],
    principle: [
      '一条授权 = 一个主体（全员 / 角色 / 用户）+ 一个数据集 + 一个有效期，落在 ai_agent_resource_permissions 里。',
      '判定时取「本人授权 ∪ 本人所有角色的授权 ∪ 全员基线」，多个角色取并集——角色越多，能看到的数据集只增不减。',
      '用户级授权覆盖角色级：可以给某个人单独放宽，也可以把某个人的范围收窄，不必改角色。',
      '全员基线是不可收窄的公共底线：一旦授了全员，任何人的个人授权都无法把「能进」改回「不能进」。',
      '过期的授权不再参与判定，但记录保留——续期只需改日期，不用重配范围和行级条件。',
    ],
    pitfall: '这一层通过 ≠ 能看到所有表。还要再过表级（能查哪些表）和行级（能看哪些行）两道。',
  },
  table: {
    title: '表级 · 能查哪些表',
    subtitle: '决定「进了这个数据集，能碰哪几张表」',
    impact: [
      '同一个数据集里，这个主体能看到哪些表的表结构、能把哪些表写进 SQL 和 join。',
      'SQL 里出现授权范围外的表，整条查询会被拒绝，而不是悄悄把那张表过滤掉——悄悄过滤会让人以为数据缺失。',
    ],
    principle: [
      '每条授权的表范围只有两种：全部表，或指定表的白名单（选 N 张具体的表）。',
      '白名单是 fail-closed：如果写成「指定表」却一张都没选，等于一张都不给，而不是退化成全部表。',
      '多个角色的白名单取并集；用户级授权覆盖角色级，所以给个人收窄是有效的。',
      '新同步进来的表：授权是「全部表」时自动包含；已经收窄成白名单时，新表默认不可见，页面顶部会出现「N 张新表待确认」。',
      '执行时按每张表自己的归属数据集校验，跨数据集 join 的附表同样拦得住。',
    ],
    pitfall: '数据集级有权限、表级又收窄了白名单，最终只能查白名单里的表——两层的交集，不是并集。',
  },
  row: {
    title: '行级 · 能看哪些行',
    subtitle: '决定「同一张表里，能看到哪些行」',
    impact: [
      '查询返回的数据行会被自动加上 WHERE 条件，例如只能看到本部门的订单。',
      '对导出、看板、定时报表同样生效——它们和 ChatBI 走的是同一条执行链。',
    ],
    principle: [
      '每条授权选一种策略：继承数据集策略 / 不过滤 / 自定义条件。',
      '继承数据集策略：用数据集自身的行级配置，优先级是「用户例外 → 角色策略 → 默认策略」。',
      '不过滤：显式跳过行级过滤，能看到可见表的所有行（慎用，等于对这一条授权放弃了行级隔离）。',
      '自定义条件：把这条授权自己的 SQL 条件拼进 WHERE，和数据集自身的配置无关。',
      '重写发生在 SQL 执行前：先解析 SQL 结构、再改 WHERE，不是简单的字符串拼接，所以子查询和 join 里的表也能加条件。',
    ],
    pitfall: '自定义条件如果引用了本次查询里没有的表、无法生效，系统会直接拒绝执行，而不是返回全量数据。',
  },
}

const scopeHelp = computed(() => (scopeHelpKey.value ? SCOPE_HELP[scopeHelpKey.value] : null))

// 权限模拟要调管理端接口（需要 element:metadata:edit），只读用户看不到入口；
// 否则点进去只会拿到 403。
const viewTabs = computed(() => allViewTabs)

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
          @click="openSimulateModal"
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
        <p class="flex items-center gap-1.5 text-[11px] font-semibold text-gray-400 select-none">
          数据集级 · 谁能进
          <button
            type="button"
            class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-gray-300 text-[9px] font-bold leading-none text-gray-400 transition-colors hover:border-emerald-400 hover:text-emerald-600"
            title="这一层管什么、怎么判定"
            @click.stop="scopeHelpKey = 'dataset'"
          >?</button>
        </p>
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
        <p class="flex items-center gap-1.5 text-[11px] font-semibold text-gray-400 select-none">
          表级 · 能查哪些表
          <button
            type="button"
            class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-gray-300 text-[9px] font-bold leading-none text-gray-400 transition-colors hover:border-emerald-400 hover:text-emerald-600"
            title="这一层管什么、怎么判定"
            @click.stop="scopeHelpKey = 'table'"
          >?</button>
        </p>
        <p class="mt-1.5 text-xl font-bold text-gray-900">
          {{ tableScopeStats.covered }}
          <span class="text-xs font-medium text-gray-400">/ {{ tables.length }} 张被显式点名</span>
        </p>
        <p class="mt-1 text-[11px] text-gray-500">
          限定范围 {{ tableScopeStats.listCount }} 条 · 全部表 {{ tableScopeStats.allCount }} 条
        </p>
      </div>
      <div class="rounded-xl border border-gray-100 bg-gradient-to-br from-white to-amber-50/40 p-3.5">
        <p class="flex items-center gap-1.5 text-[11px] font-semibold text-gray-400 select-none">
          行级 · 能看哪些行
          <button
            type="button"
            class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-gray-300 text-[9px] font-bold leading-none text-gray-400 transition-colors hover:border-emerald-400 hover:text-emerald-600"
            title="这一层管什么、怎么判定"
            @click.stop="scopeHelpKey = 'row'"
          >?</button>
        </p>
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

    <!-- 视图 3：数据行策略 -->
    <!-- 字段范围：行 = 表，列 = 授权主体，单元格 = 可见字段 / 总字段 -->
    <div v-else-if="view === 'columns'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2">
        <label class="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-600">
          <input v-model="onlyColNarrowed" type="checkbox" class="accent-emerald-600">
          只看做了字段收窄的表（{{ colNarrowedTables.length }}）
        </label>
        <span class="ml-auto text-[10.5px] text-slate-500">
          同一张表，不同主体可见的字段可以不同；<b>·</b> = 该主体看不到这张表
        </span>
      </div>

      <div v-if="!grants.length" class="rounded-xl border border-dashed border-gray-200 py-8 text-center text-xs text-gray-400">
        还没有任何授权条目
      </div>
      <div v-else class="overflow-x-auto rounded-xl border border-gray-200">
        <table class="w-full text-[11px]">
          <thead class="bg-gray-50/80">
            <tr>
              <th class="whitespace-nowrap px-3 py-2 text-left font-semibold text-gray-600">物理表</th>
              <th v-for="g in grants" :key="g.key" class="whitespace-nowrap px-3 py-2 text-center font-semibold text-gray-600">
                {{ g.label }}
                <span v-if="g.status === 'expired'" class="block text-[9.5px] font-normal text-red-500">已过期</span>
              </th>
              <th class="whitespace-nowrap px-3 py-2 text-center font-semibold text-gray-600">被收窄主体</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in columnMatrixRows" :key="row.table.id" class="border-t border-gray-100">
              <td class="px-3 py-1.5">
                <span class="font-mono text-gray-700">{{ row.table.physical_name }}</span>
                <span class="ml-1.5 text-[10px] text-gray-400">{{ row.table.term || '' }}</span>
              </td>
              <td v-for="cell in row.cells" :key="cell.grant.key" class="px-3 py-1.5 text-center">
                <span v-if="cell.visible === null" class="text-gray-300">·</span>
                <span
                  v-else
                  class="inline-flex min-w-[42px] justify-center rounded-md border px-1.5 py-0.5 font-mono tabular-nums"
                  :class="cell.visible < cell.total
                    ? 'border-amber-300 bg-amber-50 font-semibold text-amber-700'
                    : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
                >{{ cell.visible }}/{{ cell.total }}</span>
              </td>
              <td class="px-3 py-1.5 text-center text-gray-500">{{ row.narrowedBy || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2 text-[10.5px] leading-relaxed text-slate-600">
        <b>合并规则</b>：字段级取<b>交集</b> —— 用户级 / 角色级 / 全员任何一层排除了某个字段，这个字段就不可见。
        方向与表级相反是有意的：表级「全员基线不可收窄」保证不能凭空少给权限；字段级反过来，
        全员排除掉的敏感字段不能被个人配置重新放开，而个人对某张表的收窄也必须生效。
      </p>
    </div>

    <div v-else-if="view === 'rows'" class="space-y-3">
      <div class="rounded-xl border border-gray-100 bg-white p-3.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-xs font-bold text-gray-700">
            数据集自身数据行策略
            <span class="ml-1 rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500">只读</span>
          </h3>
          <div class="flex flex-wrap items-center gap-2">
            <span
              class="inline-flex items-center gap-1.5 rounded-lg border px-2 py-0.5 text-[10px] font-semibold"
              :class="rowFilterEnabled ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
            >
              <span class="h-1.5 w-1.5 rounded-full" :class="rowFilterEnabled ? 'bg-amber-500' : 'bg-gray-300'"></span>
              {{ rowFilterEnabled ? `已启用校验 · ${datasetRowRuleCount} 条规则` : '未启用精细化权限校验' }}
            </span>
            <!-- 这一块是只读展示，真正能改的是元数据管理里的行级策略弹窗。
                 没权限的人不给入口，避免点了撞 403。 -->
            <button
              v-if="canEdit"
              type="button"
              class="inline-flex items-center gap-1 rounded-lg border border-emerald-600 bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-emerald-700"
              title="去元数据管理编辑该数据集的行级策略"
              @click="emit('edit-row-policy')"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
              编辑行级策略
            </button>
          </div>
        </div>
        <p class="mt-1 text-[11px] text-gray-400">
          选「继承数据集策略」的授权会走这里的规则：用户例外 → 角色策略 → 默认规则。
          这里只读展示，避免和「按人覆盖」混淆；要改规则点右上角「编辑行级策略」。
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
        <h3 class="text-xs font-bold text-gray-700">三种数据行策略</h3>
        <ul class="mt-2 space-y-1.5 text-[11px] text-gray-600">
          <li><span class="font-semibold text-gray-700">继承数据集策略</span>（默认）：走数据集自身的行级配置（用户例外 → 角色策略 → 默认策略），在「数据行策略」里维护，改动对所有继承的人生效。</li>
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
              v-for="(label, idx) in ['主体', '表范围', '字段', '行级', '有效期']"
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
              <span v-if="idx < 4" class="mx-0.5 text-gray-300">›</span>
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
                type="search"
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
                    type="search"
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

            <!-- Step 3 字段范围 -->
            <div v-else-if="drawerStep === 2" class="space-y-2">
              <p class="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2 text-[11px] leading-relaxed text-slate-600">
                作用在表范围之上，逐表设置。<b>没配置的表 = 全部字段</b>，只有你要收窄的表才需要动。
                字段收窄是 fail-closed 的：被排除的字段不会出现在 AI 可见的表结构里，查询里引用了会被拒绝。
              </p>

              <div class="max-h-56 overflow-y-auto rounded-xl border border-gray-200">
                <div
                  v-for="t in draftActiveTables"
                  :key="t.id"
                  class="flex items-center gap-2 border-b border-gray-50 px-3 py-1.5 text-[11px] last:border-b-0"
                >
                  <span class="font-mono" :class="colRuleOf(t.id) ? 'font-semibold text-amber-700' : 'text-gray-700'">{{ t.physical_name }}</span>
                  <span class="truncate text-[10px] text-gray-400">{{ t.term || '' }}</span>
                  <span class="ml-auto" :class="colRuleOf(t.id) ? 'font-semibold text-amber-700' : 'text-gray-400'">{{ colModeText(t.id) }}</span>
                  <button class="text-[11px] font-medium text-gray-500 hover:text-gray-800" @click="openColDetail(t.id)">
                    {{ colRuleOf(t.id) ? '调整' : '设置' }}
                  </button>
                  <button
                    v-if="colRuleOf(t.id)"
                    class="text-[11px] text-gray-400 hover:text-gray-600"
                    @click="clearColRule(t.id)"
                  >还原</button>
                </div>
                <p v-if="!draftActiveTables.length" class="px-3 py-3 text-center text-[11px] text-gray-400">
                  {{ draft.scopeMode === 'list' ? '请先在上一步勾选要授权的表' : '该数据集暂无可用表' }}
                </p>
              </div>

              <!-- 字段勾选面板 -->
              <div v-if="colDetailTable" class="rounded-xl border border-gray-200 p-3">
                <div class="mb-2 flex flex-wrap items-center gap-2">
                  <span class="text-xs font-semibold text-gray-800">{{ colDetailTable.physical_name }}</span>
                  <span class="text-[10px] text-gray-500">
                    <template v-if="colRuleOf(colDetailTable.id)">
                      {{ colRuleOf(colDetailTable.id)!.mode === 'include' ? '限定' : '排除' }}
                      {{ colRuleOf(colDetailTable.id)!.names.length }} 个
                    </template>
                    <template v-else>全部字段 · 勾选任意字段开始收窄</template>
                  </span>
                  <div class="ml-auto flex items-center gap-1">
                    <button
                      class="rounded-md px-2 py-1 text-[10.5px] font-medium transition-colors"
                      :class="colMode === 'include' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600'"
                      @click="setColMode('include')"
                    >限定字段</button>
                    <button
                      class="rounded-md px-2 py-1 text-[10.5px] font-medium transition-colors"
                      :class="colMode === 'exclude' ? 'bg-amber-600 text-white' : 'bg-gray-100 text-gray-600'"
                      @click="setColMode('exclude')"
                    >排除字段</button>
                    <button class="ml-1 text-[11px] text-gray-400 hover:text-gray-600" @click="colDetailTableId = null">收起</button>
                  </div>
                </div>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="name in colDetailTable.columns || []"
                    :key="name"
                    class="rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition-colors"
                    :class="
                      colRuleOf(colDetailTable.id)?.names.includes(name)
                        ? colMode === 'exclude'
                          ? 'border-amber-300 bg-amber-50 text-amber-700 line-through'
                          : 'border-emerald-300 bg-emerald-50 font-semibold text-emerald-700'
                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                    "
                    @click="toggleCol(name)"
                  >{{ name }}</button>
                </div>
                <p v-if="colMode === 'exclude'" class="mt-2 text-[10px] text-amber-700">
                  勾选的字段<b>不可见</b>。全员/角色层排除掉的敏感字段，个人配置不能重新放开。
                </p>
                <p v-else class="mt-2 text-[10px] text-gray-500">
                  勾选的字段<b>可见</b>，其余全部隐藏。以后新增的列默认不可见（fail-closed）。
                </p>
              </div>
            </div>

            <!-- Step 4 行级 -->
            <div v-else-if="drawerStep === 3" class="space-y-2">
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

            <!-- Step 5 有效期 -->
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
              <div
                v-if="expiryFallback"
                class="flex items-start gap-1.5 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-[11px] leading-relaxed text-amber-800"
              >
                <svg class="mt-0.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M4.515 19h14.97a1 1 0 00.864-1.503l-7.485-12.97a1 1 0 00-1.728 0L3.651 17.497A1 1 0 004.515 19z" />
                </svg>
                <div>
                  <p class="font-semibold">到期后这条授权整行失效，权限会变大</p>
                  <p class="mt-0.5">
                    <template v-if="expiryFallback.definite">
                      届时改由<strong>{{ expiryFallback.fallback }}</strong>接管 ——
                      <template v-if="expiryFallback.tableWider">
                        现在是 {{ expiryFallback.tableCount }} 张表，到期后是<strong>{{ expiryFallback.certain.replace('全员授权', '') }}</strong>。
                      </template>
                      <template v-if="expiryFallback.rowWider">
                        现在配的自定义行级条件也会失效，回落到接管方的行级策略。
                      </template>
                    </template>
                    <template v-else>
                      若该用户还属于本数据集上已授权的角色，届时将改由<strong>角色授权</strong>接管，
                      实际权限可能比现在更大。请确认角色授权是否也该一并收窄。
                    </template>
                  </p>
                  <p class="mt-1 text-[10px] text-amber-700/80">
                    若要「到期即彻底收回」，请选「长期有效」后手动移除，或同时收窄上述授权。
                  </p>
                </div>
              </div>
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
                v-if="drawerStep < 4"
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
    <!-- 三层门禁说明：卡片上的数字看不出「这层到底管什么」 -->
    <Teleport to="body">
      <div
        v-if="scopeHelp"
        class="fixed inset-0 z-[110] flex items-start justify-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm sm:p-8"
        @click.self="scopeHelpKey = null"
      >
        <div class="my-auto w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-gray-100 bg-gray-50/60 px-5 py-3.5">
            <div>
              <h3 class="text-sm font-bold text-gray-800">{{ scopeHelp.title }}</h3>
              <p class="mt-0.5 text-[11px] text-gray-400">{{ scopeHelp.subtitle }}</p>
            </div>
            <button
              type="button"
              class="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-white hover:text-gray-700"
              @click="scopeHelpKey = null"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div class="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-4">
            <div>
              <p class="mb-2 text-[11px] font-bold uppercase tracking-wide text-emerald-600">影响什么</p>
              <ul class="space-y-1.5">
                <li
                  v-for="(item, idx) in scopeHelp.impact"
                  :key="`impact-${idx}`"
                  class="flex gap-2 text-[12px] leading-relaxed text-gray-600"
                >
                  <span class="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-emerald-400"></span>
                  <span>{{ item }}</span>
                </li>
              </ul>
            </div>
            <div>
              <p class="mb-2 text-[11px] font-bold uppercase tracking-wide text-sky-600">什么原理</p>
              <ul class="space-y-1.5">
                <li
                  v-for="(item, idx) in scopeHelp.principle"
                  :key="`principle-${idx}`"
                  class="flex gap-2 text-[12px] leading-relaxed text-gray-600"
                >
                  <span class="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-sky-400"></span>
                  <span>{{ item }}</span>
                </li>
              </ul>
            </div>
            <div class="rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-2.5">
              <p class="text-[11px] font-bold text-amber-800">容易踩的坑</p>
              <p class="mt-1 text-[11.5px] leading-relaxed text-amber-900">{{ scopeHelp.pitfall }}</p>
            </div>
          </div>
          <div class="flex items-center justify-between border-t border-gray-100 bg-gray-50/60 px-5 py-3">
            <span class="text-[10.5px] text-gray-400">三层是逐层收窄：能进 → 能查哪些表 → 能看哪些行</span>
            <button
              class="rounded-xl bg-gray-900 px-4 py-2 text-xs font-semibold text-white transition-all hover:bg-gray-800"
              @click="scopeHelpKey = null"
            >知道了</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 权限模拟（弹框）：查一个具体成员在这个数据集里最终能看到什么 -->
    <Teleport to="body">
      <div
        v-if="simModalOpen"
        class="fixed inset-0 z-[110] flex items-start justify-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm sm:p-8"
        @click.self="closeSimModal"
      >
        <div class="my-auto w-full max-w-3xl overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-gray-100 bg-gray-50/60 px-5 py-3.5">
            <div>
              <h3 class="text-sm font-bold text-gray-800">权限模拟</h3>
              <p class="mt-0.5 text-[11px] text-gray-400">
                选一个成员，看他在这个数据集里最终能看到哪些表、哪些行，以及命中了哪些授权。
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-white hover:text-gray-700"
              @click="closeSimModal"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div class="max-h-[70vh] space-y-3 overflow-y-auto p-4">
      <div class="flex flex-wrap items-end gap-2 rounded-xl border border-gray-100 bg-gray-50/50 p-3">
        <div class="min-w-[16rem] flex-1">
          <label class="mb-1 block text-[11px] font-semibold text-gray-500">选择成员</label>
          <div class="relative" @click.stop>
            <button
              type="button"
              class="flex w-full items-center justify-between gap-2 rounded-xl border bg-white px-3 py-2 text-left text-xs transition-colors"
              :class="simOpen ? 'border-emerald-300 ring-2 ring-emerald-100' : 'border-gray-200 hover:border-gray-300'"
              @click="toggleSimOpen"
            >
              <span class="truncate" :class="simSelectedLabel ? 'text-gray-800' : 'text-gray-400'">
                {{ simSelectedLabel || '请选择要模拟的成员' }}
              </span>
              <svg
                class="h-4 w-4 shrink-0 text-gray-400 transition-transform"
                :class="simOpen ? 'rotate-180' : ''"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            <div
              v-if="simOpen"
              class="absolute left-0 right-0 top-full z-30 mt-1 overflow-hidden rounded-xl border border-gray-100 bg-white shadow-xl"
            >
              <div class="border-b border-gray-100 p-2">
                <input
                  ref="simSearchRef"
                  v-model="simQuery"
                  type="search"
                  placeholder="搜索姓名或账号"
                  class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs focus:border-emerald-300 focus:outline-none"
                  @keydown.esc.stop="closeSimPicker"
                  @keydown.enter.prevent="simFilteredUsers.length && pickSimUser(simFilteredUsers[0])"
                />
              </div>
              <div class="max-h-64 overflow-y-auto py-1">
                <button
                  v-for="u in simFilteredUsers"
                  :key="u.id"
                  type="button"
                  class="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-gray-50"
                  :class="Number(u.id) === simUserId ? 'font-semibold text-emerald-700' : 'text-gray-700'"
                  @click="pickSimUser(u)"
                >
                  <span class="truncate">{{ u.real_name || u.user_name }}</span>
                  <span
                    v-if="u.real_name && u.real_name !== u.user_name"
                    class="shrink-0 text-[10px] text-gray-400"
                  >{{ u.user_name }}</span>
                </button>
                <p v-if="!simFilteredUsers.length" class="px-3 py-4 text-center text-[11px] text-gray-400">
                  没有匹配的成员
                </p>
              </div>
            </div>
          </div>
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
          <div class="flex items-center justify-end gap-2 border-t border-gray-100 bg-gray-50/60 px-5 py-3">
            <button class="rounded-xl border border-gray-200 px-4 py-2 text-xs font-semibold text-gray-600 transition-colors hover:bg-white" @click="closeSimModal">关闭</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>

  <ConfirmModal
    v-if="pendingRemoval"
    title="移除授权"
    :message="`确认移除「${pendingRemoval.label}」在该数据集上的授权？`"
    confirm-text="移除"
    type="danger"
    @confirm="confirmRemoval"
    @cancel="cancelRemoval"
  />
</template>
