<script setup lang="ts">
/**
 * 数据集「复制新建」向导。
 *
 * 六步：基本信息（ID / 名称实时查重、数据源只能从已配置连接里选）→ 数据表与字段 →
 * 表关系 → 指标 → 复制选项 → 确认保存。
 *
 * 中间所有编辑都在本地草稿里，关掉即丢弃；只有最后一步调一次 clone 接口，
 * 后端单事务建出数据集与全部子资源并写初始化变更日志。
 */
import { ref, reactive, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  metadataApi,
  type Dataset,
  type DbConnectionConfig,
  type CloneDatasetPayload,
  type CloneUniqueField,
} from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import {
  XMarkIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CheckIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  ArrowPathIcon,
  DocumentDuplicateIcon,
} from '@heroicons/vue/24/outline'

const props = defineProps<{
  show: boolean
  dataset: Dataset | null
}>()

const emit = defineEmits<{ (e: 'close'): void; (e: 'created', dataset: Dataset): void }>()

const router = useRouter()
const { showToast } = useToast()

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------
interface DraftColumn {
  physical_name: string
  term: string
  type: string
  description?: string
  enums?: any[]
  synonyms?: string[]
  is_primary?: boolean
  dimension_role?: string
  hierarchy_group?: string | null
  hierarchy_order?: number | null
}

interface DraftTable {
  physical_name: string
  term: string
  description?: string
  synonyms?: string[]
  partition_fields?: string[]
  index_fields?: string[]
  columns: DraftColumn[]
}

interface DraftRelationship {
  source_table: string
  target_table: string
  join_condition: string
  join_type: string
  description?: string
}

interface DraftMetric {
  name: string
  display_name: string
  description?: string
  calculation_logic: string
  unit?: string
  tags?: string[]
}

interface CheckState {
  state: 'idle' | 'checking' | 'ok' | 'taken' | 'bad'
  message: string
}

const STEPS = [
  { n: 1, title: '基本信息', desc: 'ID / 名称 / 数据源' },
  { n: 2, title: '数据表与字段', desc: '核对 · 可改可删' },
  { n: 3, title: '表关系', desc: '自动重映射' },
  { n: 4, title: '指标口径', desc: '可改可删可加' },
  { n: 5, title: '复制选项', desc: '权限 / 日志' },
  { n: 6, title: '确认保存', desc: '最后一步落库' },
]

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const loading = ref(false)
const loadError = ref('')
const step = ref(1)
const maxStep = ref(1)
const phase = ref<'form' | 'saving' | 'done'>('form')

const source = ref<Dataset | null>(null)
const dataSources = ref<DbConnectionConfig[]>([])
const dataSourceDenied = ref(false)
const tagInput = ref('')

const draft = reactive({
  name: '',
  display_name: '',
  description: '',
  tags: [] as string[],
  data_source: '',
  tables: [] as DraftTable[],
  relationships: [] as DraftRelationship[],
  metrics: [] as DraftMetric[],
})

const opts = reactive({
  copyDesc: true,
  copyTags: true,
  copyPerm: false,
  logMode: 'detail' as 'detail' | 'summary',
  jump: true,
})

const unique = reactive<{ name: CheckState; display_name: CheckState }>({
  name: { state: 'idle', message: '' },
  display_name: { state: 'idle', message: '' },
})

const removedTables = ref<DraftTable[]>([])
const removedColumns = ref<Array<{ table: string; column: DraftColumn; index: number }>>([])
const tableQuery = ref('')
const expanded = ref<Record<string, boolean>>({})
const editingTableIndex = ref<number | null>(null)
const editBuf = reactive({ term: '', description: '' })
const dsOpen = ref(false)

type DrawerState =
  | { type: 'column'; tableIndex: number; columnIndex: number } // columnIndex = -1 表示新增
  | { type: 'metric'; index: number }
  | { type: 'table'; index: number }

const drawer = ref<DrawerState | null>(null)

// 抽屉里的草稿副本：新增和编辑共用一套表单，保存时才写回数组
const columnBuf = reactive<DraftColumn>({
  physical_name: '',
  term: '',
  type: 'String',
  description: '',
  enums: [],
  synonyms: [],
  is_primary: false,
  dimension_role: 'none',
  hierarchy_group: null,
  hierarchy_order: null,
})

const tableBuf = reactive({ physical_name: '', term: '', description: '' })
const metricBuf = reactive<DraftMetric>({ name: '', display_name: '', description: '', calculation_logic: '', unit: '', tags: [] })

const saving = ref(false)
const saveIndex = ref(0)
const saveSteps = ref<string[]>([])
const created = ref<{ id: number; name: string; display_name: string } | null>(null)
const createdSummary = ref<{ tables: number; columns: number; relationships: number; metrics: number; skipped_relationships: number } | null>(null)

let checkTimer: ReturnType<typeof setTimeout> | null = null
let checkSeq = 0
let saveTimer: ReturnType<typeof setInterval> | null = null

// ---------------------------------------------------------------------------
// 打开 / 加载
// ---------------------------------------------------------------------------
watch(
  () => props.show,
  (visible) => {
    if (visible) void open()
  },
)

watch(
  () => [draft.name, draft.display_name],
  () => {
    if (!props.show || phase.value !== 'form') return
    scheduleCheck()
  },
)

async function open() {
  if (!props.dataset) return
  phase.value = 'form'
  step.value = 1
  maxStep.value = 1
  loading.value = true
  loadError.value = ''
  removedTables.value = []
  removedColumns.value = []
  tableQuery.value = ''
  expanded.value = {}
  editingTableIndex.value = null
  drawer.value = null
  dsOpen.value = false

  try {
    const res = await metadataApi.getDataset(props.dataset.id)
    const detail: any = res.data
    source.value = { ...props.dataset, ...detail }

    const tables: DraftTable[] = (detail.tables || []).map((t: any) => ({
      physical_name: t.physical_name,
      term: t.term || t.physical_name,
      description: t.description || '',
      synonyms: [...(t.synonyms || [])],
      partition_fields: [...(t.partition_fields || [])],
      index_fields: [...(t.index_fields || [])],
      columns: (t.columns || []).map((c: any) => ({
        physical_name: c.physical_name,
        term: c.term || c.physical_name,
        type: c.type || 'String',
        description: c.description || '',
        enums: Array.isArray(c.enums) ? JSON.parse(JSON.stringify(c.enums)) : [],
        synonyms: [...(c.synonyms || [])],
        is_primary: !!c.is_primary,
        dimension_role: c.dimension_role || 'none',
        hierarchy_group: c.hierarchy_group ?? null,
        hierarchy_order: c.hierarchy_order ?? null,
      })),
    }))

    const idToName = new Map<number, string>()
    for (const t of detail.tables || []) idToName.set(t.id, t.physical_name)

    const relationships: DraftRelationship[] = (detail.relationships || [])
      .map((r: any) => ({
        source_table: idToName.get(r.source_table_id) || '',
        target_table: idToName.get(r.target_table_id) || '',
        join_condition: r.join_condition || '',
        join_type: (r.join_type || 'LEFT').toUpperCase(),
        description: r.description || '',
      }))
      .filter((r: DraftRelationship) => r.source_table && r.target_table)

    const metrics: DraftMetric[] = (detail.metrics || []).map((m: any) => ({
      name: m.name,
      display_name: m.display_name || m.name,
      description: m.description || '',
      calculation_logic: m.calculation_logic || '',
      unit: m.unit || '',
      tags: [...(m.tags || [])],
    }))

    draft.tables = tables
    draft.relationships = relationships
    draft.metrics = metrics
    draft.description = detail.description || ''
    draft.tags = [...(detail.tags || [])]
    draft.data_source = detail.data_source || props.dataset.data_source || ''

    // 默认名与显示名：先占位成候选，再用一次查重确认是否真的可用
    draft.name = `${props.dataset.name}_copy`
    draft.display_name = `${props.dataset.display_name || props.dataset.name}（副本）`
    expanded.value = tables.length ? { [tables[0].physical_name]: true } : {}

    await Promise.all([loadDataSources(), confirmSuggestedNames()])
    await runCheck()
  } catch (e: any) {
    loadError.value = e?.response?.data?.detail || e?.message || '加载数据集详情失败'
  } finally {
    loading.value = false
  }
}

async function loadDataSources() {
  try {
    const res = await metadataApi.listDbConnectionConfigs()
    dataSources.value = res.data?.data || []
    dataSourceDenied.value = false
  } catch (e: any) {
    // 只有 metadata:edit 的用户没有数据源管理权限：退化为「只能沿用当前数据源」
    dataSources.value = []
    dataSourceDenied.value = e?.response?.status === 403
  }
}

/** 候选 ID / 名称被占用时自动往后找，保证打开向导就是一个可用的默认值。 */
async function confirmSuggestedNames() {
  const base = props.dataset?.name || 'dataset'
  const displayBase = props.dataset?.display_name || props.dataset?.name || '数据集'
  const idCandidates = ['_copy', '_copy2', '_copy3', '_bak']
  const displayCandidates = ['（副本）', '（副本 2）', '（副本 3）', '（镜像）']

  try {
    const idRes = await metadataApi.checkDatasetUnique({ name: `${base}${idCandidates[0]}` })
    if (!idRes.data.data.name.available) {
      for (const suffix of idCandidates.slice(1)) {
        const candidate = `${base}${suffix}`
        const res = await metadataApi.checkDatasetUnique({ name: candidate })
        if (res.data.data.name.available) {
          draft.name = candidate
          break
        }
      }
    }
  } catch {
    /* 查重失败不阻塞：后面还有一次完整校验 */
  }

  try {
    for (const suffix of displayCandidates) {
      const candidate = `${displayBase}${suffix}`
      const res = await metadataApi.checkDatasetUnique({ display_name: candidate })
      if (res.data.data.display_name.available) {
        draft.display_name = candidate
        break
      }
    }
  } catch {
    /* 同上 */
  }
}

// ---------------------------------------------------------------------------
// 实时查重
// ---------------------------------------------------------------------------
function scheduleCheck() {
  if (checkTimer) clearTimeout(checkTimer)
  unique.name = { state: 'checking', message: '' }
  unique.display_name = { state: 'checking', message: '' }
  checkTimer = setTimeout(() => void runCheck(), 350)
}

function applyField(target: CheckState, server: CloneUniqueField | undefined) {
  if (!server) {
    target.state = 'bad'
    target.message = '校验失败，请重试'
    return
  }
  if (server.available) {
    target.state = 'ok'
    target.message = server.message || '可用，全平台唯一'
    return
  }
  target.state = server.reason === 'taken' ? 'taken' : 'bad'
  target.message = server.message || '不可用'
}

async function runCheck() {
  if (!props.show) return
  const seq = ++checkSeq
  unique.name = { state: 'checking', message: '' }
  unique.display_name = { state: 'checking', message: '' }
  try {
    const res = await metadataApi.checkDatasetUnique({
      name: draft.name.trim(),
      display_name: draft.display_name.trim(),
    })
    if (seq !== checkSeq) return
    applyField(unique.name, res.data?.data?.name)
    applyField(unique.display_name, res.data?.data?.display_name)
  } catch (e: any) {
    if (seq !== checkSeq) return
    const detail = e?.response?.data?.detail || e?.message || '查重失败'
    unique.name = { state: 'bad', message: detail }
    unique.display_name = { state: 'bad', message: detail }
  }
}

const checking = computed(
  () => unique.name.state === 'checking' || unique.display_name.state === 'checking',
)

const stepOneValid = computed(
  () =>
    unique.name.state === 'ok' &&
    unique.display_name.state === 'ok' &&
    !!draft.data_source,
)

const canProceed = computed(() => {
  if (phase.value !== 'form') return false
  if (step.value === 1) return stepOneValid.value
  if (step.value === 2) return draft.tables.length > 0
  // 回到第 6 步时也要保证前面改过的内容仍然合法（用户可能改坏 ID 再跳回来）
  if (step.value === 6) return stepOneValid.value && draft.tables.length > 0
  return true
})

/** 步骤条状态：done / active / todo */
function stepState(n: number): 'done' | 'active' | 'todo' {
  if (phase.value === 'saving') return n === 6 ? 'active' : 'done'
  if (phase.value === 'done') return 'done'
  if (n < step.value) return 'done'
  if (n === step.value) return 'active'
  return 'todo'
}

// ---------------------------------------------------------------------------
// 派生数据
// ---------------------------------------------------------------------------
const tableNameSet = computed(() => new Set(draft.tables.map((t) => t.physical_name)))

const validRelationships = computed(() =>
  draft.relationships.filter(
    (r) => tableNameSet.value.has(r.source_table) && tableNameSet.value.has(r.target_table),
  ),
)

const brokenRelationshipCount = computed(
  () => draft.relationships.length - validRelationships.value.length,
)

const columnCount = computed(() =>
  draft.tables.reduce((total, t) => total + t.columns.length, 0),
)

/** 带原始下标的过滤结果：模板里不要再对 table 做 indexOf（每次渲染都是 O(n²)） */
const filteredTables = computed(() => {
  const q = tableQuery.value.trim().toLowerCase()
  const rows = draft.tables.map((table, index) => ({ table, index }))
  if (!q) return rows
  return rows.filter(
    ({ table }) =>
      table.physical_name.toLowerCase().includes(q) ||
      (table.term || '').toLowerCase().includes(q) ||
      table.columns.some(
        (c) => c.physical_name.toLowerCase().includes(q) || (c.term || '').toLowerCase().includes(q),
      ),
  )
})

const currentDataSource = computed(
  () => dataSources.value.find((d) => d.name === draft.data_source) || null,
)

const dataSourceChanged = computed(
  () => !!source.value && draft.data_source !== source.value.data_source,
)

const logPreview = computed(() => {
  const logs: Array<{ op: string; resource: string; text: string }> = [
    {
      op: 'CREATE',
      resource: `meta_dataset#${draft.name}`,
      text: `由「${source.value?.display_name || ''}(${source.value?.name || ''})」复制新建 · ${draft.tables.length} 表 / ${columnCount.value} 字段 / ${validRelationships.value.length} 关系 / ${draft.metrics.length} 指标 · 数据源 ${draft.data_source}`,
    },
  ]
  if (opts.logMode === 'detail') {
    for (const t of draft.tables) {
      logs.push({
        op: 'CREATE',
        resource: `meta_table#${t.physical_name}`,
        text: `随数据集复制创建 · ${t.columns.length} 个字段 · 源表 ${t.physical_name}`,
      })
    }
    for (const r of validRelationships.value) {
      logs.push({
        op: 'CREATE',
        resource: `meta_relationship#${r.source_table}→${r.target_table}`,
        text: `${r.source_table} → ${r.target_table} · ${r.join_type} JOIN`,
      })
    }
    for (const m of draft.metrics) {
      logs.push({ op: 'CREATE', resource: `meta_metric#${m.name}`, text: `${m.display_name} · ${m.calculation_logic}` })
    }
  }
  return logs
})

/** 后端实际会写入的日志条数（与 buildPayload 的 log_mode 保持一致） */
const writtenLogCount = computed(() => (opts.logMode === 'detail' ? logPreview.value.length : 1))

const stepBadge = computed<Record<number, string>>(() => ({
  2: `${draft.tables.length} 张`,
  3: `${validRelationships.value.length} 条`,
  4: `${draft.metrics.length} 个`,
}))

// ---------------------------------------------------------------------------
// 步骤操作
// ---------------------------------------------------------------------------
function goStep(target: number) {
  if (phase.value !== 'form') return
  if (target > maxStep.value) {
    showToast('先完成前面的步骤', 'info')
    return
  }
  step.value = target
}

function nextStep() {
  if (!canProceed.value) return
  if (step.value < 6) {
    step.value += 1
    maxStep.value = Math.max(maxStep.value, step.value)
  }
}

function prevStep() {
  if (step.value > 1) step.value -= 1
}

// 复制选项开关
function toggleOpt(key: 'copyDesc' | 'copyTags' | 'copyPerm') {
  opts[key] = !opts[key]
}

// 标签
function addTag() {
  const value = tagInput.value.trim()
  if (!value) return
  if (!draft.tags.includes(value)) draft.tags.push(value)
  tagInput.value = ''
}

function removeTag(index: number) {
  draft.tags.splice(index, 1)
}

// 数据源
function pickDataSource(name: string) {
  draft.data_source = name
  dsOpen.value = false
}

// 表
function toggleTable(name: string) {
  expanded.value[name] = !expanded.value[name]
}

function startEditTable(index: number) {
  editingTableIndex.value = index
  editBuf.term = draft.tables[index].term
  editBuf.description = draft.tables[index].description || ''
}

function saveEditTable() {
  const index = editingTableIndex.value
  if (index === null) return
  draft.tables[index].term = editBuf.term.trim() || draft.tables[index].physical_name
  draft.tables[index].description = editBuf.description
  editingTableIndex.value = null
}

function removeTable(index: number) {
  const [removed] = draft.tables.splice(index, 1)
  if (!removed) return
  removedTables.value.push(removed)
  removedColumns.value = removedColumns.value.filter((item) => item.table !== removed.physical_name)
  // 抽屉 / 行内编辑都按数组下标定位，删表后必须同步，否则会指向错行
  editingTableIndex.value = null
  if (drawer.value?.type === 'column') {
    if (drawer.value.tableIndex === index) drawer.value = null
    else if (drawer.value.tableIndex > index) drawer.value.tableIndex -= 1
  }
  showToast(`已从副本移除表「${removed.physical_name}」`, 'info')
}

function removeColumn(tableIndex: number, columnIndex: number) {
  const table = draft.tables[tableIndex]
  if (!table) return
  const [removed] = table.columns.splice(columnIndex, 1)
  if (!removed) return
  removedColumns.value.push({
    table: table.physical_name,
    column: removed,
    index: columnIndex,
  })
  if (
    drawer.value?.type === 'column' &&
    drawer.value.tableIndex === tableIndex &&
    drawer.value.columnIndex === columnIndex
  ) {
    drawer.value = null
  }
  showToast(`已移除字段「${table.physical_name}.${removed.physical_name}」`, 'info')
}

function restoreColumns() {
  // 同一张表删了多个字段时按原下标从小到大插回，否则恢复顺序会错位
  const sorted = [...removedColumns.value].sort((a, b) => a.index - b.index)
  for (const item of sorted) {
    const table = draft.tables.find((t) => t.physical_name === item.table)
    if (!table) continue
    table.columns.splice(Math.min(item.index, table.columns.length), 0, item.column)
  }
  removedColumns.value = []
  showToast('已恢复移除的字段', 'success')
}

function restoreTables() {
  draft.tables.push(...removedTables.value)
  removedTables.value = []
  showToast('已恢复移除的表', 'success')
}

// 关系
function removeRelationship(index: number) {
  draft.relationships.splice(index, 1)
}

function cleanBrokenRelationships() {
  const names = tableNameSet.value
  draft.relationships = draft.relationships.filter(
    (r) => names.has(r.source_table) && names.has(r.target_table),
  )
}

// 字段抽屉：columnIndex 传 null 表示新增
function openColumn(tableIndex: number, columnIndex: number | null) {
  const table = draft.tables[tableIndex]
  if (!table) return
  if (columnIndex === null) {
    Object.assign(columnBuf, {
      physical_name: '',
      term: '',
      type: 'String',
      description: '',
      enums: [],
      synonyms: [],
      is_primary: false,
      dimension_role: 'none',
      hierarchy_group: null,
      hierarchy_order: null,
    })
    drawer.value = { type: 'column', tableIndex, columnIndex: -1 }
    return
  }
  const column = table.columns[columnIndex]
  if (!column) return
  Object.assign(columnBuf, JSON.parse(JSON.stringify(column)))
  drawer.value = { type: 'column', tableIndex, columnIndex }
}

function saveColumn() {
  const state = drawer.value
  if (!state || state.type !== 'column') return
  const table = draft.tables[state.tableIndex]
  if (!table) return

  const physicalName = columnBuf.physical_name.trim()
  if (!physicalName) {
    showToast('字段物理名不能为空', 'warning')
    return
  }
  const duplicated = table.columns.some(
    (item, index) => item.physical_name === physicalName && index !== state.columnIndex,
  )
  if (duplicated) {
    showToast(`字段「${physicalName}」已存在于 ${table.physical_name}`, 'warning')
    return
  }

  const payload: DraftColumn = {
    ...JSON.parse(JSON.stringify(columnBuf)),
    physical_name: physicalName,
    term: columnBuf.term.trim() || physicalName,
  }
  if (state.columnIndex >= 0) table.columns[state.columnIndex] = payload
  else table.columns.push(payload)
  drawer.value = null
}

// 新增表
function suggestTableName() {
  let index = draft.tables.length + 1
  while (draft.tables.some((t) => t.physical_name === `new_table_${index}`)) index += 1
  return `new_table_${index}`
}

function openNewTable() {
  tableBuf.physical_name = suggestTableName()
  tableBuf.term = ''
  tableBuf.description = ''
  drawer.value = { type: 'table', index: -1 }
}

function saveTable() {
  const physicalName = tableBuf.physical_name.trim()
  if (!physicalName) {
    showToast('表的物理名不能为空', 'warning')
    return
  }
  if (draft.tables.some((t) => t.physical_name === physicalName)) {
    showToast(`表「${physicalName}」已存在于副本中`, 'warning')
    return
  }
  draft.tables.push({
    physical_name: physicalName,
    term: tableBuf.term.trim() || physicalName,
    description: tableBuf.description,
    synonyms: [],
    partition_fields: [],
    index_fields: [],
    columns: [],
  })
  expanded.value[physicalName] = true
  tableQuery.value = ''
  drawer.value = null
  showToast(`已新增表「${physicalName}」（草稿）`, 'success')
}

const drawerTitle = computed(() => {
  const state = drawer.value
  if (!state) return ''
  if (state.type === 'column') return state.columnIndex >= 0 ? '编辑字段' : '新增字段'
  if (state.type === 'table') return '新增表'
  return state.index >= 0 ? '编辑指标' : '新增指标'
})

// 指标抽屉
function openMetric(index: number | null) {
  if (index === null) {
    metricBuf.name = ''
    metricBuf.display_name = ''
    metricBuf.description = ''
    metricBuf.calculation_logic = ''
    metricBuf.unit = ''
    metricBuf.tags = []
    drawer.value = { type: 'metric', index: -1 }
    return
  }
  const metric = draft.metrics[index]
  metricBuf.name = metric.name
  metricBuf.display_name = metric.display_name
  metricBuf.description = metric.description || ''
  metricBuf.calculation_logic = metric.calculation_logic
  metricBuf.unit = metric.unit || ''
  metricBuf.tags = [...(metric.tags || [])]
  drawer.value = { type: 'metric', index }
}

function saveMetric() {
  if (!drawer.value || drawer.value.type !== 'metric') return
  if (!metricBuf.name.trim() || !metricBuf.display_name.trim() || !metricBuf.calculation_logic.trim()) {
    showToast('指标标识、显示名称、计算逻辑不能为空', 'warning')
    return
  }
  const payload: DraftMetric = {
    name: metricBuf.name.trim(),
    display_name: metricBuf.display_name.trim(),
    description: metricBuf.description,
    calculation_logic: metricBuf.calculation_logic,
    unit: metricBuf.unit,
    tags: (metricBuf.tags || []).filter(Boolean),
  }
  if (drawer.value.index >= 0) draft.metrics[drawer.value.index] = payload
  else draft.metrics.push(payload)
  drawer.value = null
}

function setMetricTags(value: string) {
  metricBuf.tags = value.split(',').map((item) => item.trim()).filter(Boolean)
}

function removeMetric(index: number) {
  const [removed] = draft.metrics.splice(index, 1)
  if (removed) showToast(`已删除指标「${removed.display_name}」`, 'info')
}

// ---------------------------------------------------------------------------
// 保存
// ---------------------------------------------------------------------------
function buildPayload(): CloneDatasetPayload {
  return {
    name: draft.name.trim(),
    display_name: draft.display_name.trim(),
    description: opts.copyDesc ? draft.description : '',
    tags: opts.copyTags ? [...draft.tags] : [],
    data_source: draft.data_source,
    tables: draft.tables.map((t) => ({
      physical_name: t.physical_name,
      term: t.term,
      description: t.description,
      synonyms: [...(t.synonyms || [])],
      partition_fields: [...(t.partition_fields || [])],
      index_fields: [...(t.index_fields || [])],
      columns: t.columns.map((c) => ({
        physical_name: c.physical_name,
        term: c.term,
        type: c.type,
        description: c.description,
        enums: c.enums || [],
        synonyms: [...(c.synonyms || [])],
        is_primary: !!c.is_primary,
        dimension_role: c.dimension_role || 'none',
        hierarchy_group: c.hierarchy_group ?? null,
        hierarchy_order: c.hierarchy_order ?? null,
      })),
    })),
    relationships: validRelationships.value.map((r) => ({
      source_table: r.source_table,
      target_table: r.target_table,
      join_condition: r.join_condition,
      join_type: r.join_type,
      description: r.description,
    })),
    metrics: draft.metrics.map((m) => ({
      name: m.name,
      display_name: m.display_name,
      description: m.description,
      calculation_logic: m.calculation_logic,
      unit: m.unit,
      tags: [...(m.tags || [])],
    })),
    copy_permissions: opts.copyPerm,
    log_mode: opts.logMode,
  }
}

async function submit() {
  if (!props.dataset || !canProceed.value) return
  phase.value = 'saving'
  saving.value = true
  saveIndex.value = 0
  saveSteps.value = [
    '校验 ID / 名称唯一性',
    `创建数据集 ${draft.name.trim()}`,
    `写入 ${draft.tables.length} 张表、${columnCount.value} 个字段`,
    `重映射并写入 ${validRelationships.value.length} 条表关系`,
    `写入 ${draft.metrics.length} 个指标`,
    `写入初始化变更日志 ${logPreview.value.length} 条`,
  ]

  saveTimer = setInterval(() => {
    if (saveIndex.value < saveSteps.value.length - 1) saveIndex.value += 1
  }, 700)

  try {
    const res = await metadataApi.cloneDataset(props.dataset.id, buildPayload())
    const payload = res.data?.data
    created.value = payload?.dataset
      ? {
          id: payload.dataset.id,
          name: payload.dataset.name,
          display_name: payload.dataset.display_name,
        }
      : null
    createdSummary.value = payload?.summary || null
    // 让进度条走完，避免「瞬间完成」看不出做过什么
    while (saveIndex.value < saveSteps.value.length) {
      await new Promise((resolve) => setTimeout(resolve, 220))
      if (saveIndex.value >= saveSteps.value.length) break
      saveIndex.value += 1
    }
    phase.value = 'done'
    if (created.value) {
      const createdDataset = payload!.dataset
      emit('created', createdDataset)
      if (opts.jump) {
        setTimeout(() => void openCreatedDataset('dataset'), 1200)
      }
    }
  } catch (e: any) {
    phase.value = 'form'
    saving.value = false
    const message = e?.response?.data?.detail || e?.message || '复制新建失败'
    showToast(message, 'error')
    // ID / 名称在提交瞬间被别人占用：直接把人送回第 1 步改，而不是让人在确认页干瞪眼
    if (String(message).includes('占用') || String(message).includes('数据源')) {
      step.value = 1
    }
  } finally {
    if (saveTimer) {
      clearInterval(saveTimer)
      saveTimer = null
    }
    saving.value = false
  }
}

async function openCreatedDataset(target: 'dataset' | 'changelog') {
  if (!created.value) return
  const path = `/dashboard/metadata/${created.value.id}`
  emit('close')
  await nextTick()
  router.push(target === 'changelog' ? { path, query: { tab: 'changelog' } } : path)
}

// ---------------------------------------------------------------------------
// 关闭
// ---------------------------------------------------------------------------
function close() {
  if (phase.value === 'saving') return
  emit('close')
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.show && phase.value !== 'saving') close()
}

if (typeof window !== 'undefined') window.addEventListener('keydown', onKeydown)

onBeforeUnmount(() => {
  if (checkTimer) clearTimeout(checkTimer)
  if (saveTimer) clearInterval(saveTimer)
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="fixed inset-0 z-[110] flex items-center justify-center bg-slate-900/45 p-4 backdrop-blur-sm"
      @click.self="close"
    >
      <div class="relative flex h-[min(860px,92vh)] w-full max-w-[1140px] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <!-- 头部 -->
        <header class="flex shrink-0 items-center justify-between border-b border-gray-100 px-4 py-3">
          <div class="flex items-center gap-2.5">
            <div class="flex h-8 w-8 items-center justify-center rounded-lg border border-blue-100 bg-blue-50 text-blue-600">
              <DocumentDuplicateIcon class="h-4 w-4" />
            </div>
            <div>
              <h2 class="text-sm font-semibold text-gray-900">复制新建数据集</h2>
              <p class="text-[11px] text-gray-400">
                源数据集：<b class="text-gray-600">{{ source?.display_name }}（{{ source?.name }}）</b>
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <span class="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700">
              草稿 · 未写入任何数据
            </span>
            <button
              type="button"
              class="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-600"
              title="关闭（草稿会被丢弃）"
              @click="close"
            >
              <XMarkIcon class="h-4 w-4" />
            </button>
          </div>
        </header>

        <div class="relative flex min-h-0 flex-1">
          <!-- 步骤条 -->
          <aside class="w-[218px] shrink-0 overflow-y-auto border-r border-gray-100 bg-gray-50/60 px-2.5 py-3">
            <button
              v-for="item in STEPS"
              :key="item.n"
              type="button"
              class="mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors"
              :class="[
                phase === 'form' && step === item.n ? 'bg-blue-50' : 'hover:bg-gray-100/70',
                item.n > maxStep && phase === 'form' ? 'cursor-not-allowed opacity-55' : 'cursor-pointer',
              ]"
              @click="goStep(item.n)"
            >
              <span
                class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold"
                :class="
                  stepState(item.n) === 'done'
                    ? 'border-green-200 bg-green-50 text-green-600'
                    : stepState(item.n) === 'active'
                      ? 'border-blue-600 bg-blue-600 text-white'
                      : 'border-gray-300 bg-white text-gray-400'
                "
              >
                <CheckIcon v-if="stepState(item.n) === 'done'" class="h-3 w-3" />
                <template v-else>{{ item.n }}</template>
              </span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-xs font-semibold text-gray-700">{{ item.title }}</span>
                <span class="block truncate text-[10px] text-gray-400">{{ item.desc }}</span>
              </span>
              <span v-if="stepBadge[item.n]" class="shrink-0 text-[10px] text-gray-400">{{ stepBadge[item.n] }}</span>
            </button>
          </aside>

          <!-- 内容 -->
          <main class="min-w-0 flex-1 overflow-y-auto px-5 py-4">
            <div v-if="loading" class="flex h-full items-center justify-center gap-2 text-xs text-gray-400">
              <ArrowPathIcon class="h-4 w-4 animate-spin" /> 正在读取源数据集…
            </div>

            <div v-else-if="loadError" class="rounded-lg border border-red-100 bg-red-50 p-4 text-xs text-red-600">
              {{ loadError }}
            </div>

            <template v-else>
              <!-- 保存中 -->
              <div v-if="phase === 'saving'" class="pt-6">
                <h3 class="text-sm font-semibold text-gray-900">正在创建副本…</h3>
                <p class="mt-1 text-[11px] text-gray-400">单事务执行：任何一步失败都会整体回滚，不会留下半个数据集。</p>
                <div class="mt-4 space-y-1.5">
                  <div
                    v-for="(label, index) in saveSteps"
                    :key="index"
                    class="flex h-10 items-center gap-2.5 rounded-lg border px-3 text-xs"
                    :class="
                      index < saveIndex
                        ? 'border-gray-100 bg-white text-gray-500'
                        : index === saveIndex
                          ? 'border-blue-200 bg-blue-50/60 text-gray-800'
                          : 'border-gray-100 bg-white text-gray-300'
                    "
                  >
                    <span
                      class="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border text-[9px]"
                      :class="
                        index < saveIndex
                          ? 'border-green-500 bg-green-500 text-white'
                          : index === saveIndex
                            ? 'border-blue-500 text-blue-500'
                            : 'border-gray-200 text-gray-300'
                      "
                    >
                      <CheckIcon v-if="index < saveIndex" class="h-2.5 w-2.5" />
                      <ArrowPathIcon v-else-if="index === saveIndex" class="h-2.5 w-2.5 animate-spin" />
                    </span>
                    <span class="truncate">{{ label }}</span>
                    <span class="ml-auto shrink-0 text-[10px] text-gray-400">
                      {{ index < saveIndex ? '完成' : index === saveIndex ? '执行中' : '等待' }}
                    </span>
                  </div>
                </div>
              </div>

              <!-- 成功 -->
              <div v-else-if="phase === 'done'" class="flex h-full flex-col items-center justify-center py-10 text-center">
                <div class="mb-3 flex h-14 w-14 items-center justify-center rounded-full border border-green-200 bg-green-50 text-green-600">
                  <CheckIcon class="h-6 w-6" />
                </div>
                <h3 class="text-base font-semibold text-gray-900">复制新建成功</h3>
                <p class="mt-1 text-xs text-gray-500">副本已创建，初始化变更日志已写入。</p>

                <div class="mt-5 inline-flex flex-col gap-1 rounded-xl border border-gray-100 bg-gray-50/60 px-5 py-3 text-left">
                  <div class="text-xs text-gray-500">新数据集：<b class="text-gray-900">{{ created?.display_name }}</b></div>
                  <div class="text-xs text-gray-500">
                    元数据 ID：<code class="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-[11px] text-blue-600">{{ created?.name }}</code>
                  </div>
                  <div v-if="createdSummary" class="text-[11px] text-gray-500">
                    已写入 {{ createdSummary.tables }} 张表 · {{ createdSummary.columns }} 个字段 ·
                    {{ createdSummary.relationships }} 条关系 · {{ createdSummary.metrics }} 个指标 ·
                    {{ writtenLogCount }} 条初始化日志
                  </div>
                  <div class="text-[11px] text-gray-400">RAG 同步状态：未同步 · 质量分：待巡检</div>
                </div>

                <div class="mt-5 flex gap-2">
                  <button
                    type="button"
                    class="rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50"
                    @click="openCreatedDataset('changelog')"
                  >
                    查看初始化变更日志
                  </button>
                  <button
                    type="button"
                    class="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-blue-700"
                    @click="openCreatedDataset('dataset')"
                  >
                    打开数据集
                  </button>
                </div>
              </div>

              <!-- 第 1 步 -->
              <div v-else-if="step === 1">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 1 步 · 基本信息</h3>
                  <p class="mt-1 text-[11px] text-gray-400">给新数据集起一个不重复的 ID 和名称。改名只影响副本，源数据集保持原样。</p>
                </div>

                <div class="mb-4 flex items-center gap-2.5 rounded-lg border border-blue-100 bg-blue-50/40 px-3 py-2.5">
                  <DocumentDuplicateIcon class="h-4 w-4 shrink-0 text-blue-500" />
                  <div class="min-w-0 text-xs">
                    <b class="text-gray-800">正在复制「{{ source?.display_name }}」</b>
                    <span class="ml-2 text-[11px] text-gray-400">
                      {{ source?.name }} · {{ draft.tables.length }} 张表 · {{ columnCount }} 个字段 ·
                      {{ draft.relationships.length }} 条关系 · {{ draft.metrics.length }} 个指标
                    </span>
                  </div>
                </div>

                <div class="grid grid-cols-2 gap-x-4 gap-y-3">
                  <!-- ID -->
                  <div>
                    <label class="mb-1 flex items-center gap-1 text-xs font-semibold text-gray-600">
                      元数据 ID <span class="text-red-500">*</span>
                    </label>
                    <div class="flex gap-1.5">
                      <input
                        v-model="draft.name"
                        type="text"
                        autocomplete="off"
                        spellcheck="false"
                        class="min-w-0 flex-1 rounded-lg border border-gray-200 px-2.5 py-2 font-mono text-xs outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
                      />
                      <button
                        type="button"
                        class="shrink-0 rounded-lg border border-gray-200 px-2.5 text-[11px] font-semibold text-gray-500 hover:border-blue-400 hover:text-blue-600"
                        @click="draft.name = `${source?.name}_copy`; runCheck()"
                      >
                        重置
                      </button>
                    </div>
                    <div
                      class="mt-1 flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px]"
                      :class="
                        unique.name.state === 'ok'
                          ? 'bg-green-50 text-green-600'
                          : unique.name.state === 'checking'
                            ? 'bg-gray-50 text-gray-500'
                            : unique.name.state === 'idle'
                              ? 'bg-gray-50 text-gray-400'
                              : 'bg-red-50 text-red-600'
                      "
                    >
                      <ArrowPathIcon v-if="unique.name.state === 'checking'" class="h-3 w-3 animate-spin" />
                      <CheckIcon v-else-if="unique.name.state === 'ok'" class="h-3 w-3" />
                      <span v-else class="h-1.5 w-1.5 rounded-full bg-current" />
                      <span class="min-w-0 flex-1 truncate">{{ unique.name.message || '正在校验是否重复…' }}</span>
                    </div>
                    <p class="mt-1 text-[10px] leading-relaxed text-gray-400">
                      小写字母开头，允许 a-z、0-9、_，4–64 位；用于接口与 RAG 同步时的唯一标识。
                    </p>
                  </div>

                  <!-- 显示名称 -->
                  <div>
                    <label class="mb-1 flex items-center gap-1 text-xs font-semibold text-gray-600">
                      显示名称 <span class="text-red-500">*</span>
                    </label>
                    <input
                      v-model="draft.display_name"
                      type="text"
                      autocomplete="off"
                      class="w-full rounded-lg border border-gray-200 px-2.5 py-2 text-xs outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
                    />
                    <div
                      class="mt-1 flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px]"
                      :class="
                        unique.display_name.state === 'ok'
                          ? 'bg-green-50 text-green-600'
                          : unique.display_name.state === 'checking'
                            ? 'bg-gray-50 text-gray-500'
                            : unique.display_name.state === 'idle'
                              ? 'bg-gray-50 text-gray-400'
                              : 'bg-red-50 text-red-600'
                      "
                    >
                      <ArrowPathIcon v-if="unique.display_name.state === 'checking'" class="h-3 w-3 animate-spin" />
                      <CheckIcon v-else-if="unique.display_name.state === 'ok'" class="h-3 w-3" />
                      <span v-else class="h-1.5 w-1.5 rounded-full bg-current" />
                      <span class="min-w-0 flex-1 truncate">{{ unique.display_name.message || '正在校验是否重复…' }}</span>
                    </div>
                    <p class="mt-1 text-[10px] leading-relaxed text-gray-400">
                      给业务同事看的名称，会出现在数据集列表与 ChatBI 选择器里。
                    </p>
                  </div>

                  <!-- 数据源 -->
                  <div class="relative">
                    <label class="mb-1 flex items-center gap-1 text-xs font-semibold text-gray-600">
                      数据源 <span class="text-red-500">*</span>
                    </label>
                    <button
                      type="button"
                      class="flex w-full items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-left text-xs transition hover:border-gray-300"
                      @click="dsOpen = !dsOpen"
                    >
                      <span class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-700">
                        {{ currentDataSource?.db_type || 'DB' }}
                      </span>
                      <span class="min-w-0 flex-1 truncate font-mono">{{ draft.data_source }}</span>
                      <span class="text-[10px] text-gray-400">▾</span>
                    </button>

                    <div
                      v-if="dsOpen"
                      class="absolute left-0 right-0 top-full z-50 mt-1.5 max-h-64 overflow-y-auto rounded-lg border border-gray-100 bg-white p-1 shadow-xl"
                    >
                      <button
                        v-for="item in dataSources"
                        :key="item.id"
                        type="button"
                        class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-gray-50"
                        :class="item.name === draft.data_source ? 'bg-blue-50' : ''"
                        @click="pickDataSource(item.name)"
                      >
                        <span class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-700">
                          {{ item.db_type }}
                        </span>
                        <span class="min-w-0 flex-1">
                          <span class="block truncate font-mono text-[11px] text-gray-800">{{ item.name }}</span>
                          <span class="block truncate text-[10px] text-gray-400">
                            {{ item.description || item.database_name }}
                          </span>
                        </span>
                        <CheckIcon v-if="item.name === draft.data_source" class="h-3.5 w-3.5 text-blue-600" />
                      </button>
                      <div v-if="!dataSources.length" class="px-2 py-2 text-[11px] text-gray-400">
                        暂无已配置的数据源，仅可沿用当前连接
                      </div>
                    </div>

                    <p class="mt-1 text-[10px] leading-relaxed text-gray-400">
                      只能从「数据源管理」里已配置的连接中选择，不支持手填。
                      <span v-if="dataSourceDenied" class="text-amber-600">你没有数据源管理权限，仅可沿用当前连接。</span>
                    </p>
                    <p
                      v-if="dataSourceChanged"
                      class="mt-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] leading-relaxed text-amber-700"
                    >
                      已更换数据源：表 / 字段定义仍复制自源数据集，物理表在新库中可能不存在，保存后建议先跑一次 Schema 巡检。
                    </p>
                  </div>

                  <!-- 标签 -->
                  <div>
                    <label class="mb-1 block text-xs font-semibold text-gray-600">业务标签</label>
                    <div class="mb-1.5 flex flex-wrap gap-1.5">
                      <span
                        v-for="(tag, index) in draft.tags"
                        :key="tag"
                        class="inline-flex items-center gap-1 rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700"
                      >
                        {{ tag }}
                        <button type="button" class="text-blue-400 hover:text-red-500" @click="removeTag(index)">✕</button>
                      </span>
                      <span v-if="!draft.tags.length" class="text-[11px] text-gray-300">暂无业务标签</span>
                    </div>
                    <div class="flex gap-1.5">
                      <input
                        v-model="tagInput"
                        type="text"
                        placeholder="输入业务标签后回车"
                        class="min-w-0 flex-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs outline-none focus:border-blue-500"
                        @keyup.enter="addTag"
                      />
                      <button
                        type="button"
                        class="shrink-0 rounded-lg border border-gray-200 px-2.5 text-[11px] font-semibold text-gray-500 hover:border-blue-400 hover:text-blue-600"
                        @click="addTag"
                      >
                        添加
                      </button>
                    </div>
                  </div>

                  <!-- 描述 -->
                  <div class="col-span-2">
                    <label class="mb-1 block text-xs font-semibold text-gray-600">描述</label>
                    <textarea
                      v-model="draft.description"
                      rows="3"
                      class="w-full resize-y rounded-lg border border-gray-200 px-2.5 py-2 text-xs leading-relaxed outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
                    />
                  </div>
                </div>

                <div class="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                  <b>提示：</b>复制只会带走<b>元数据定义</b>（表、字段、关系、指标），不会复制底层数据、不会复制 RAGFlow 里的数据集，也不会复制质量分。新数据集保存后是<b>未同步</b>状态。
                </div>
              </div>

              <!-- 第 2 步 -->
              <div v-else-if="step === 2">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 2 步 · 数据表与字段</h3>
                  <p class="mt-1 text-[11px] text-gray-400">逐张核对要带过去的内容。改术语、改描述、删整张表都在草稿里完成。</p>
                </div>

                <div class="mb-3 flex flex-wrap items-center gap-2">
                  <div class="flex w-60 items-center gap-1.5 rounded-lg border border-gray-200 px-2 py-1.5">
                    <MagnifyingGlassIcon class="h-3.5 w-3.5 text-gray-400" />
                    <input
                      v-model="tableQuery"
                      type="search"
                      placeholder="搜索表名 / 字段名 / 术语"
                      class="min-w-0 flex-1 bg-transparent text-xs outline-none"
                    />
                  </div>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-[11px] font-semibold text-blue-600 hover:bg-blue-100"
                    @click="openNewTable"
                  >
                    <PlusIcon class="h-3.5 w-3.5" /> 新增表
                  </button>
                  <button
                    type="button"
                    class="rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] font-semibold text-gray-500 hover:border-blue-400 hover:text-blue-600"
                    @click="draft.tables.forEach((t) => (expanded[t.physical_name] = true))"
                  >
                    展开全部字段
                  </button>
                  <button
                    type="button"
                    class="rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] font-semibold text-gray-500 hover:border-blue-400 hover:text-blue-600"
                    @click="expanded = {}"
                  >
                    收起
                  </button>
                  <button
                    v-if="removedTables.length"
                    type="button"
                    class="rounded-lg border border-blue-200 px-2.5 py-1.5 text-[11px] font-semibold text-blue-600 hover:bg-blue-50"
                    @click="restoreTables"
                  >
                    恢复已移除的 {{ removedTables.length }} 张表
                  </button>
                  <button
                    v-if="removedColumns.length"
                    type="button"
                    class="rounded-lg border border-blue-200 px-2.5 py-1.5 text-[11px] font-semibold text-blue-600 hover:bg-blue-50"
                    @click="restoreColumns"
                  >
                    恢复已移除的 {{ removedColumns.length }} 个字段
                  </button>
                  <span class="ml-auto text-[11px] text-gray-400">
                    共 <b class="text-gray-700">{{ draft.tables.length }}</b> 张表，当前显示
                    <b class="text-gray-700">{{ filteredTables.length }}</b> 张
                  </span>
                </div>

                <div v-if="!filteredTables.length" class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  {{ tableQuery ? `没有匹配「${tableQuery}」的表或字段。` : '已经没有任何表了，复制一个空数据集没有意义，请至少保留一张表。' }}
                  <button
                    v-if="!tableQuery"
                    type="button"
                    class="ml-2 rounded border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-amber-700"
                    @click="openNewTable"
                  >
                    新增一张表
                  </button>
                </div>

                <div
                  v-for="row in filteredTables"
                  :key="row.table.physical_name"
                  class="mb-2 overflow-hidden rounded-xl border"
                  :class="editingTableIndex === row.index ? 'border-blue-200' : 'border-gray-100'"
                >
                  <div class="flex items-center gap-2 px-3 py-2">
                    <button
                      type="button"
                      class="text-[10px] text-gray-400 transition-transform"
                      :class="expanded[row.table.physical_name] ? 'rotate-90' : ''"
                      @click="toggleTable(row.table.physical_name)"
                    >
                      ▶
                    </button>
                    <span class="font-mono text-xs font-semibold text-gray-800">{{ row.table.physical_name }}</span>
                    <span class="text-[11px] text-gray-500">{{ row.table.term }}</span>
                    <span
                      class="rounded-full border px-1.5 py-0.5 text-[10px]"
                      :class="
                        row.table.columns.length
                          ? 'border-gray-200 bg-gray-50 text-gray-400'
                          : 'border-amber-200 bg-amber-50 text-amber-600'
                      "
                    >
                      {{ row.table.columns.length ? `${row.table.columns.length} 字段` : '无字段' }}
                    </span>
                    <div class="ml-auto flex items-center gap-1">
                      <button
                        type="button"
                        class="rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-blue-50 hover:text-blue-600"
                        @click="openColumn(row.index, null)"
                      >
                        + 字段
                      </button>
                      <button
                        type="button"
                        class="rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-gray-50 hover:text-blue-600"
                        @click="editingTableIndex === row.index ? (editingTableIndex = null) : startEditTable(row.index)"
                      >
                        {{ editingTableIndex === row.index ? '取消编辑' : '编辑' }}
                      </button>
                      <button
                        type="button"
                        class="rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-red-50 hover:text-red-600"
                        @click="removeTable(row.index)"
                      >
                        删除
                      </button>
                    </div>
                  </div>

                  <div
                    v-if="editingTableIndex === row.index"
                    class="grid grid-cols-2 gap-2 border-t border-gray-100 bg-gray-50/60 px-3 py-2"
                  >
                    <div>
                      <label class="mb-0.5 block text-[10px] font-semibold text-gray-400">业务术语</label>
                      <input v-model="editBuf.term" type="text" class="w-full rounded-md border border-gray-200 px-2 py-1 text-xs outline-none focus:border-blue-500" />
                    </div>
                    <div>
                      <label class="mb-0.5 block text-[10px] font-semibold text-gray-400">表描述</label>
                      <input v-model="editBuf.description" type="text" class="w-full rounded-md border border-gray-200 px-2 py-1 text-xs outline-none focus:border-blue-500" />
                    </div>
                    <div class="col-span-2 flex justify-end gap-1.5">
                      <button type="button" class="rounded-md border border-gray-200 px-2.5 py-1 text-[11px] text-gray-500" @click="editingTableIndex = null">
                        取消
                      </button>
                      <button type="button" class="rounded-md border border-blue-300 bg-blue-50 px-2.5 py-1 text-[11px] font-semibold text-blue-600" @click="saveEditTable">
                        保存修改
                      </button>
                    </div>
                  </div>

                  <table v-if="expanded[row.table.physical_name]" class="w-full table-fixed border-t border-gray-100 text-[11px]">
                    <thead>
                      <tr class="bg-gray-50/70 text-[10px] text-gray-400">
                        <th class="w-[150px] px-2.5 py-1.5 text-left font-semibold">物理字段</th>
                        <th class="w-[120px] px-2.5 py-1.5 text-left font-semibold">业务术语</th>
                        <th class="w-[78px] px-2.5 py-1.5 text-left font-semibold">类型</th>
                        <th class="px-2.5 py-1.5 text-left font-semibold">描述</th>
                        <th class="w-[88px] px-2.5 py-1.5 text-left font-semibold">维度角色</th>
                        <th class="w-[86px] px-2.5 py-1.5" />
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(column, columnIndex) in row.table.columns"
                        :key="column.physical_name"
                        class="border-t border-gray-50 text-gray-500 hover:bg-gray-50/40"
                      >
                        <td class="px-2.5 py-1.5">
                          <span class="font-mono text-gray-700">{{ column.physical_name }}</span>
                          <span v-if="column.is_primary" class="ml-1 rounded border border-amber-200 bg-amber-50 px-1 text-[9px] font-bold text-amber-600">PK</span>
                        </td>
                        <td class="truncate px-2.5 py-1.5">{{ column.term }}</td>
                        <td class="px-2.5 py-1.5 font-mono text-gray-400">{{ column.type }}</td>
                        <td class="truncate px-2.5 py-1.5">{{ column.description }}</td>
                        <td class="px-2.5 py-1.5">
                          <span v-if="column.dimension_role && column.dimension_role !== 'none'" class="rounded bg-gray-50 px-1 text-[10px] text-gray-400">
                            {{ column.dimension_role }}
                          </span>
                          <span v-else class="text-gray-300">—</span>
                        </td>
                        <td class="px-2.5 py-1.5">
                          <div class="flex items-center gap-0.5">
                            <button
                              type="button"
                              class="rounded px-1 text-[11px] text-gray-500 hover:bg-gray-100 hover:text-blue-600"
                              @click="openColumn(row.index, columnIndex)"
                            >
                              编辑
                            </button>
                            <button
                              type="button"
                              class="rounded px-1 text-[11px] text-gray-500 hover:bg-red-50 hover:text-red-600"
                              @click="removeColumn(row.index, columnIndex)"
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    </tbody>
                  </table>

                  <div
                    v-if="expanded[row.table.physical_name] && !row.table.columns.length"
                    class="border-t border-gray-100 px-3 py-2 text-[11px] text-gray-400"
                  >
                    这张表还没有字段，点右上角「+ 字段」添加。
                  </div>
                </div>

                <div v-if="!draft.tables.length" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  已经没有任何表了。复制一个空数据集没有意义，请至少保留一张表。
                </div>
              </div>

              <!-- 第 3 步 -->
              <div v-else-if="step === 3">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 3 步 · 表关系</h3>
                  <p class="mt-1 text-[11px] text-gray-400">关系会在保存时<b>自动按新表重映射</b>；依赖的表被删掉时会标红并在保存时跳过。</p>
                </div>

                <div v-if="brokenRelationshipCount" class="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  有 <b>{{ brokenRelationshipCount }}</b> 条关系依赖的表已经在第 2 步被移除，保存时会被跳过。
                  <button type="button" class="ml-2 rounded border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-amber-700" @click="cleanBrokenRelationships">
                    清理失效关系
                  </button>
                </div>

                <div class="mb-2 text-[11px] text-gray-400">
                  有效关系 <b class="text-gray-700">{{ validRelationships.length }}</b> / 共 {{ draft.relationships.length }} 条
                </div>

                <div v-if="!draft.relationships.length" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  没有表关系。跨表分析时模型会拿不到 JOIN 依据，建议至少保留一条。
                </div>

                <div
                  v-for="(relation, index) in draft.relationships"
                  :key="`${relation.source_table}-${relation.target_table}-${index}`"
                  class="mb-2 rounded-xl border px-3 py-2"
                  :class="
                    tableNameSet.has(relation.source_table) && tableNameSet.has(relation.target_table)
                      ? 'border-gray-100'
                      : 'border-red-200 bg-red-50/40'
                  "
                >
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[11px] text-gray-700">
                      {{ relation.source_table }}
                    </span>
                    <span class="text-[11px] text-gray-400">⟶</span>
                    <span class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[11px] text-gray-700">
                      {{ relation.target_table }}
                    </span>
                    <span class="rounded bg-purple-50 px-1.5 py-0.5 text-[10px] font-bold text-purple-600">
                      {{ relation.join_type }} JOIN
                    </span>
                    <span
                      v-if="tableNameSet.has(relation.source_table) && tableNameSet.has(relation.target_table)"
                      class="rounded border border-green-200 bg-green-50 px-1.5 py-0.5 text-[10px] text-green-600"
                    >
                      ✓ 有效
                    </span>
                    <span v-else class="rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[10px] font-bold text-red-600">
                      依赖表已移除
                    </span>
                    <button
                      type="button"
                      class="ml-auto rounded px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-red-50 hover:text-red-600"
                      @click="removeRelationship(index)"
                    >
                      移除
                    </button>
                  </div>
                  <div class="mt-2 grid grid-cols-[110px_1fr_auto] items-center gap-2">
                    <select v-model="relation.join_type" class="rounded-md border border-gray-200 px-2 py-1 text-[11px] outline-none focus:border-blue-500">
                      <option value="LEFT">LEFT JOIN</option>
                      <option value="INNER">INNER JOIN</option>
                      <option value="RIGHT">RIGHT JOIN</option>
                    </select>
                    <input
                      v-model="relation.description"
                      type="text"
                      placeholder="关系说明，如：按客户 ID 关联主档"
                      class="rounded-md border border-gray-200 px-2 py-1 text-[11px] outline-none focus:border-blue-500"
                    />
                    <span class="text-[10px] text-gray-400">
                      {{ tableNameSet.has(relation.source_table) && tableNameSet.has(relation.target_table) ? '保存时重映射' : '将被跳过' }}
                    </span>
                  </div>
                  <p class="mt-1 truncate font-mono text-[10px] text-gray-400" :title="relation.join_condition">
                    {{ relation.join_condition }}
                  </p>
                </div>
              </div>

              <!-- 第 4 步 -->
              <div v-else-if="step === 4">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 4 步 · 指标口径</h3>
                  <p class="mt-1 text-[11px] text-gray-400">指标表达式按物理表名书写，复制后保持不变；改了表名记得回来同步表达式。</p>
                </div>

                <div class="mb-3 flex items-center gap-2">
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] font-semibold text-gray-600 hover:border-blue-400 hover:text-blue-600"
                    @click="openMetric(null)"
                  >
                    <PlusIcon class="h-3.5 w-3.5" /> 新增指标
                  </button>
                  <span class="ml-auto text-[11px] text-gray-400">共 <b class="text-gray-700">{{ draft.metrics.length }}</b> 个指标</span>
                </div>

                <div v-for="(metric, index) in draft.metrics" :key="metric.name" class="mb-2 rounded-xl border border-gray-100 px-3 py-2">
                  <div class="flex items-center gap-2">
                    <span class="font-mono text-xs font-semibold text-gray-800">{{ metric.name }}</span>
                    <span class="text-[11px] text-gray-500">{{ metric.display_name }}</span>
                    <div class="ml-auto flex gap-1">
                      <button type="button" class="rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-gray-50 hover:text-blue-600" @click="openMetric(index)">
                        编辑
                      </button>
                      <button type="button" class="rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-red-50 hover:text-red-600" @click="removeMetric(index)">
                        删除
                      </button>
                    </div>
                  </div>
                  <p class="mt-1 text-[11px] text-gray-400">{{ metric.description }}</p>
                  <pre class="mt-1.5 overflow-x-auto whitespace-pre rounded-md border border-amber-100 bg-amber-50/50 px-2 py-1 font-mono text-[10px] text-amber-800">{{ metric.calculation_logic }}</pre>
                  <div class="mt-1.5 flex items-center gap-2 text-[10px] text-gray-400">
                    <span>单位：{{ metric.unit || '—' }}</span>
                    <span v-for="tag in metric.tags" :key="tag" class="rounded bg-gray-50 px-1.5 py-0.5">{{ tag }}</span>
                  </div>
                </div>

                <div v-if="!draft.metrics.length" class="rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2 text-[11px] text-gray-400">
                  这个数据集没有指标，可以跳过。
                </div>
              </div>

              <!-- 第 5 步 -->
              <div v-else-if="step === 5">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 5 步 · 复制选项</h3>
                  <p class="mt-1 text-[11px] text-gray-400">决定副本继承哪些属性。默认只继承内容与描述，权限需要显式打开。</p>
                </div>

                <div class="mb-3 overflow-hidden rounded-xl border border-gray-100">
                  <div class="border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] font-semibold text-gray-500">内容（第 2–4 步已确定）</div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-green-400/70" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">表与字段 · {{ draft.tables.length }} 张 / {{ columnCount }} 个</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">复制物理名、术语、类型、描述、枚举、维度角色；字段 ID 会重新生成。</div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-green-400/70" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">表关系 · {{ validRelationships.length }} 条</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">保存时按「旧表 ID → 新表 ID」映射重写 source / target，JOIN 条件原样保留。</div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-green-400/70" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">指标 · {{ draft.metrics.length }} 个</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">名称、显示名、口径、表达式、单位、标签整体复制。</div>
                    </div>
                  </div>
                </div>

                <div class="mb-3 overflow-hidden rounded-xl border border-gray-100">
                  <div class="border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] font-semibold text-gray-500">对象属性</div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <button
                      type="button"
                      class="mt-0.5 h-4 w-7 shrink-0 rounded-full transition-colors"
                      :class="opts.copyDesc ? 'bg-green-500' : 'bg-gray-300'"
                      @click="toggleOpt('copyDesc')"
                    />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">复制描述</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">把源数据集的描述带过来，方便区分副本用途。</div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <button
                      type="button"
                      class="mt-0.5 h-4 w-7 shrink-0 rounded-full transition-colors"
                      :class="opts.copyTags ? 'bg-green-500' : 'bg-gray-300'"
                      @click="toggleOpt('copyTags')"
                    />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">复制业务标签</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">保留源数据集的业务标签，可在第 1 步增删。</div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-green-400/70" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">
                        数据源 · <span class="font-mono">{{ draft.data_source }}</span>
                      </div>
                      <div class="mt-0.5 text-[11px] text-gray-400">
                        {{ dataSourceChanged ? '当前已与源数据集不同，副本的表结构需要在新库重新核对。' : '当前沿用源数据集的绑定。' }}
                        如需更换，回到第 1 步从下拉里重选（不支持手填）。
                      </div>
                    </div>
                  </div>
                </div>

                <div class="mb-3 overflow-hidden rounded-xl border border-gray-100">
                  <div class="border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] font-semibold text-gray-500">权限与状态</div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <button
                      type="button"
                      class="mt-0.5 h-4 w-7 shrink-0 rounded-full transition-colors"
                      :class="opts.copyPerm ? 'bg-green-500' : 'bg-gray-300'"
                      @click="toggleOpt('copyPerm')"
                    />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">
                        复制数据权限配置
                        <span class="ml-1 text-[9px] font-bold text-amber-600">谨慎</span>
                      </div>
                      <div class="mt-0.5 text-[11px] text-gray-400">
                        包含源数据集的行级隔离策略。默认关闭：行级规则常绑定具体人员，直接复制容易造成越权或误拦。
                      </div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 border-b border-gray-50 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-gray-300" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">复制质量分</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">质量分是源数据集巡检的结果，新数据集置空，等下一轮巡检重新打分。</div>
                    </div>
                  </div>
                  <div class="flex gap-2.5 px-3 py-2">
                    <span class="mt-0.5 h-4 w-7 shrink-0 rounded-full bg-gray-300" />
                    <div>
                      <div class="text-xs font-semibold text-gray-700">RAGFlow 同步状态</div>
                      <div class="mt-0.5 text-[11px] text-gray-400">
                        强制重置为未同步（<code class="rounded bg-gray-50 px-1">rag_sync_status = 0</code>），不复制源数据集的 RAG 数据集 ID。
                      </div>
                    </div>
                  </div>
                </div>

                <div class="overflow-hidden rounded-xl border border-gray-100">
                  <div class="border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] font-semibold text-gray-500">初始化变更日志</div>
                  <div class="px-3 py-2">
                    <div class="text-xs font-semibold text-gray-700">写入粒度</div>
                    <div class="mt-0.5 text-[11px] text-gray-400">
                      选择复制动作在变更日志里留下多少条记录。明细便于逐个资源追溯来源，汇总则更清爽。
                    </div>
                    <div class="mt-2 flex gap-1.5">
                      <button
                        type="button"
                        class="rounded-lg border px-2.5 py-1 text-[11px]"
                        :class="opts.logMode === 'detail' ? 'border-blue-300 bg-blue-50 font-semibold text-blue-600' : 'border-gray-200 text-gray-500'"
                        @click="opts.logMode = 'detail'"
                      >
                        汇总 1 条 + 明细 {{ draft.tables.length + validRelationships.length + draft.metrics.length }} 条
                      </button>
                      <button
                        type="button"
                        class="rounded-lg border px-2.5 py-1 text-[11px]"
                        :class="opts.logMode === 'summary' ? 'border-blue-300 bg-blue-50 font-semibold text-blue-600' : 'border-gray-200 text-gray-500'"
                        @click="opts.logMode = 'summary'"
                      >
                        仅 1 条汇总
                      </button>
                    </div>
                  </div>
                </div>

                <div class="mt-3 overflow-hidden rounded-xl border border-gray-100">
                  <div class="flex items-center gap-2 border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] text-gray-500">
                    日志预览 <b class="text-gray-700">{{ logPreview.length }}</b> 条 · 保存成功后写入
                  </div>
                  <div class="max-h-52 overflow-y-auto px-3 py-1.5">
                    <div v-for="(log, index) in logPreview" :key="index" class="flex items-start gap-2 border-b border-dashed border-gray-50 py-1 last:border-0">
                      <span class="shrink-0 rounded bg-blue-50 px-1 py-0.5 font-mono text-[9px] font-bold text-blue-600">{{ log.op }}</span>
                      <span class="shrink-0 font-mono text-[10px] text-gray-700">{{ log.resource }}</span>
                      <span class="min-w-0 flex-1 truncate text-[10px] text-gray-400">{{ log.text }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 第 6 步 -->
              <div v-else-if="step === 6">
                <div class="mb-3">
                  <h3 class="text-sm font-semibold text-gray-900">第 6 步 · 确认保存</h3>
                  <p class="mt-1 text-[11px] text-gray-400">这是唯一会写数据库的一步。确认后会以单事务创建数据集及其全部子资源。</p>
                </div>

                <div class="mb-3 grid grid-cols-4 gap-2">
                  <div class="rounded-lg border border-gray-100 bg-gray-50/40 px-3 py-2">
                    <b class="block text-lg leading-tight text-gray-900">{{ draft.tables.length }}</b>
                    <span class="text-[10px] text-gray-400">数据表</span>
                  </div>
                  <div class="rounded-lg border border-gray-100 bg-gray-50/40 px-3 py-2">
                    <b class="block text-lg leading-tight text-gray-900">{{ columnCount }}</b>
                    <span class="text-[10px] text-gray-400">字段</span>
                  </div>
                  <div class="rounded-lg border border-gray-100 bg-gray-50/40 px-3 py-2">
                    <b class="block text-lg leading-tight text-gray-900">{{ validRelationships.length }}</b>
                    <span class="text-[10px] text-gray-400">表关系</span>
                  </div>
                  <div class="rounded-lg border border-gray-100 bg-gray-50/40 px-3 py-2">
                    <b class="block text-lg leading-tight text-gray-900">{{ draft.metrics.length }}</b>
                    <span class="text-[10px] text-gray-400">指标</span>
                  </div>
                </div>

                <div class="grid grid-cols-2 gap-2">
                  <div class="rounded-xl border border-gray-100 px-3 py-2.5">
                    <h4 class="mb-2 text-[11px] font-semibold text-gray-400">新数据集</h4>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">元数据 ID</span><span class="font-mono text-gray-800">{{ draft.name }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">显示名称</span><span class="text-gray-800">{{ draft.display_name }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">数据源</span><span class="font-mono text-gray-800">{{ draft.data_source }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">状态</span><span class="text-gray-800">{{ source?.status === 1 ? '启用' : '停用（沿用源数据集）' }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">质量分</span><span class="text-gray-800">空 · 待巡检</span></div>
                  </div>
                  <div class="rounded-xl border border-gray-100 px-3 py-2.5">
                    <h4 class="mb-2 text-[11px] font-semibold text-gray-400">复制范围</h4>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">业务标签</span><span class="max-w-[70%] truncate text-right text-gray-800">{{ opts.copyTags && draft.tags.length ? draft.tags.join('、') : '不复制' }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">描述</span><span class="text-gray-800">{{ opts.copyDesc ? '继承源描述' : '清空' }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">数据权限</span><span class="text-gray-800">{{ opts.copyPerm ? '继承源策略' : '不复制 · 默认关闭' }}</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">RAG 同步</span><span class="text-gray-800">未同步（保存后手动同步）</span></div>
                    <div class="flex justify-between gap-2 py-0.5 text-[11px]"><span class="text-gray-400">初始化日志</span><span class="text-gray-800">{{ logPreview.length }} 条</span></div>
                  </div>
                </div>

                <div v-if="brokenRelationshipCount" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                  <b>注意：</b>{{ brokenRelationshipCount }} 条关系依赖的表已被移除，保存时会跳过，不会报错。
                </div>
                <div v-if="dataSourceChanged" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                  <b>数据源已更换：</b>{{ source?.data_source }} → <b>{{ draft.data_source }}</b>。副本的表 / 字段定义仍来自源数据集，新库里不一定存在同名物理表，建议保存后先跑一次 Schema 巡检。
                </div>
                <div v-if="!opts.copyPerm" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                  <b>权限提示：</b>源数据集若启用了行级隔离，副本不会继承，保存后直接开放查询会变成无行级过滤，请记得重新配置。
                </div>

                <div class="mt-3 overflow-hidden rounded-xl border border-gray-100">
                  <div class="border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] text-gray-500">
                    保存后写入的变更日志（{{ logPreview.length }} 条）
                  </div>
                  <div class="max-h-48 overflow-y-auto px-3 py-1.5">
                    <div v-for="(log, index) in logPreview" :key="index" class="flex items-start gap-2 border-b border-dashed border-gray-50 py-1 last:border-0">
                      <span class="shrink-0 rounded bg-blue-50 px-1 py-0.5 font-mono text-[9px] font-bold text-blue-600">{{ log.op }}</span>
                      <span class="shrink-0 font-mono text-[10px] text-gray-700">{{ log.resource }}</span>
                      <span class="min-w-0 flex-1 truncate text-[10px] text-gray-400">{{ log.text }}</span>
                    </div>
                  </div>
                </div>

                <label class="mt-2 flex items-center gap-1.5 text-xs text-gray-500">
                  <input v-model="opts.jump" type="checkbox" class="rounded border-gray-300" /> 保存成功后自动打开新数据集
                </label>
              </div>
            </template>
          </main>

          <!-- 字段 / 指标 / 新增表抽屉 -->
          <aside v-if="drawer" class="absolute inset-y-0 right-0 z-20 flex w-[420px] flex-col border-l border-gray-100 bg-white shadow-[-14px_0_32px_rgba(16,24,40,.08)]">
            <div class="flex items-center gap-2 border-b border-gray-100 px-3.5 py-2.5">
              <h4 class="text-xs font-semibold text-gray-900">{{ drawerTitle }}</h4>
              <span v-if="drawer.type === 'column'" class="text-[11px] text-gray-400">
                {{ draft.tables[drawer.tableIndex]?.physical_name }}
                <template v-if="drawer.columnIndex >= 0"> · 第 {{ drawer.columnIndex + 1 }} 列</template>
                <template v-else> · 新字段</template>
              </span>
              <button type="button" class="ml-auto text-gray-400 hover:text-gray-600" @click="drawer = null">✕</button>
            </div>

            <div class="flex-1 space-y-2.5 overflow-y-auto px-3.5 py-3">
              <!-- 字段：新增 / 编辑 -->
              <template v-if="drawer.type === 'column'">
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">物理字段名 <span class="text-red-500">*</span></label>
                  <input
                    v-model="columnBuf.physical_name"
                    type="text"
                    :readonly="drawer.columnIndex >= 0"
                    placeholder="如 resource_id"
                    class="w-full rounded-md border px-2 py-1.5 font-mono text-[11px] outline-none"
                    :class="
                      drawer.columnIndex >= 0
                        ? 'cursor-not-allowed border-gray-100 bg-gray-50 text-gray-500'
                        : 'border-gray-200 focus:border-blue-500'
                    "
                  />
                  <p v-if="drawer.columnIndex >= 0" class="mt-0.5 text-[10px] text-gray-400">
                    物理字段名复制后不可改，避免表关系里的 JOIN 条件失配。
                  </p>
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">业务术语 <span class="text-red-500">*</span></label>
                  <input v-model="columnBuf.term" type="text" placeholder="如 资源ID" class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">字段类型</label>
                  <input v-model="columnBuf.type" type="text" placeholder="String / Int64 / DateTime" class="w-full rounded-md border border-gray-200 px-2 py-1.5 font-mono text-[11px] outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">描述</label>
                  <textarea v-model="columnBuf.description" rows="3" class="w-full resize-y rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">维度角色</label>
                  <select v-model="columnBuf.dimension_role" class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500">
                    <option value="none">无（none）</option>
                    <option value="time">时间（time）</option>
                    <option value="geo">地理（geo）</option>
                    <option value="category">分类（category）</option>
                    <option value="identifier">标识（identifier）</option>
                  </select>
                </div>
                <label class="flex items-center gap-1.5 text-[11px] text-gray-600">
                  <input v-model="columnBuf.is_primary" type="checkbox" class="rounded border-gray-300" />
                  主键字段
                </label>
              </template>

              <!-- 新增表 -->
              <template v-else-if="drawer.type === 'table'">
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">物理表名 <span class="text-red-500">*</span></label>
                  <input v-model="tableBuf.physical_name" type="text" placeholder="如 dim_channel" class="w-full rounded-md border border-gray-200 px-2 py-1.5 font-mono text-[11px] outline-none focus:border-blue-500" />
                  <p class="mt-0.5 text-[10px] text-gray-400">表名要和物理库一致，否则 ChatBI 查数会找不到表。</p>
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">业务术语</label>
                  <input v-model="tableBuf.term" type="text" placeholder="如 渠道维表" class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">表描述</label>
                  <textarea v-model="tableBuf.description" rows="3" class="w-full resize-y rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <p class="rounded-md border border-blue-100 bg-blue-50/60 px-2 py-1.5 text-[10px] leading-relaxed text-blue-700">
                  新表先建出表结构，保存后可以在数据集详情里继续补字段；也可以回到本页点「+ 字段」直接加。
                </p>
              </template>

              <!-- 指标 -->
              <template v-else>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">指标标识 <span class="text-red-500">*</span></label>
                  <input v-model="metricBuf.name" type="text" placeholder="如 resource_pv" class="w-full rounded-md border border-gray-200 px-2 py-1.5 font-mono text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">显示名称 <span class="text-red-500">*</span></label>
                  <input v-model="metricBuf.display_name" type="text" placeholder="如 资源访问量" class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">业务描述</label>
                  <textarea v-model="metricBuf.description" rows="2" class="w-full resize-y rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">计算逻辑 <span class="text-red-500">*</span></label>
                  <textarea v-model="metricBuf.calculation_logic" rows="3" placeholder="SUM(fact_resource_usage.usage_count)" class="w-full resize-y rounded-md border border-gray-200 px-2 py-1.5 font-mono text-[11px] outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">单位</label>
                  <input v-model="metricBuf.unit" type="text" placeholder="次 / 人 / 秒" class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label class="mb-0.5 block text-[11px] font-semibold text-gray-500">标签（逗号分隔）</label>
                  <input
                    :value="(metricBuf.tags || []).join(', ')"
                    type="text"
                    class="w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs outline-none focus:border-blue-500"
                    @input="setMetricTags(($event.target as HTMLInputElement).value)"
                  />
                </div>
              </template>
            </div>

            <div class="flex items-center gap-2 border-t border-gray-100 px-3.5 py-2.5">
              <button
                v-if="drawer.type === 'column' && drawer.columnIndex >= 0"
                type="button"
                class="mr-auto rounded-lg px-2 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50"
                @click="removeColumn(drawer.tableIndex, drawer.columnIndex)"
              >
                删除此字段
              </button>
              <div class="ml-auto flex gap-2">
                <button type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600" @click="drawer = null">取消</button>
                <button
                  v-if="drawer.type === 'column'"
                  type="button"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                  @click="saveColumn"
                >
                  {{ drawer.columnIndex >= 0 ? '保存' : '添加' }}
                </button>
                <button
                  v-else-if="drawer.type === 'table'"
                  type="button"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                  @click="saveTable"
                >
                  添加
                </button>
                <button
                  v-else
                  type="button"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                  @click="saveMetric"
                >
                  {{ drawer.index >= 0 ? '保存' : '添加' }}
                </button>
              </div>
            </div>
          </aside>
        </div>

        <!-- 底部 -->
        <footer class="flex shrink-0 items-center gap-2 border-t border-gray-100 bg-gray-50/60 px-4 py-2.5">
          <div class="text-[11px] text-gray-400">
            <template v-if="phase === 'saving'">正在保存，请勿关闭窗口…</template>
            <template v-else-if="phase === 'done'">初始化变更日志已写入</template>
            <template v-else>
              将复制 <b class="text-gray-700">{{ draft.tables.length }}</b> 张表 ·
              <b class="text-gray-700">{{ columnCount }}</b> 个字段 ·
              <b class="text-gray-700">{{ validRelationships.length }}</b> 条关系 ·
              <b class="text-gray-700">{{ draft.metrics.length }}</b> 个指标
              <span v-if="brokenRelationshipCount" class="text-red-500"> · {{ brokenRelationshipCount }} 条关系失效</span>
            </template>
          </div>

          <div class="ml-auto flex items-center gap-2">
            <template v-if="phase === 'done'">
              <button type="button" class="rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-gray-600" @click="close">关闭</button>
              <button type="button" class="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-blue-700" @click="openCreatedDataset('dataset')">
                打开新数据集
              </button>
            </template>
            <template v-else>
              <button
                v-if="step > 1"
                type="button"
                class="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50"
                @click="prevStep"
              >
                <ChevronLeftIcon class="h-3.5 w-3.5" /> 上一步
              </button>
              <button
                v-if="step < 6"
                type="button"
                class="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45"
                :disabled="!canProceed || checking"
                @click="nextStep"
              >
                下一步 <ChevronRightIcon class="h-3.5 w-3.5" />
              </button>
              <button
                v-else
                type="button"
                class="inline-flex items-center gap-1 rounded-lg bg-green-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-45"
                :disabled="!canProceed || phase === 'saving'"
                @click="submit"
              >
                确认保存并复制
              </button>
            </template>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
