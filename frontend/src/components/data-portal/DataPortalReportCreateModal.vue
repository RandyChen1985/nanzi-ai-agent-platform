<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { basicSetup } from 'codemirror'
import { sql } from '@codemirror/lang-sql'
import { EditorState } from '@codemirror/state'
import { EditorView, placeholder } from '@codemirror/view'
import {
  XMarkIcon,
  PlayIcon,
  CheckCircleIcon,
  ExclamationCircleIcon,
  DocumentPlusIcon,
  ArrowPathIcon,
  CircleStackIcon,
} from '@heroicons/vue/24/outline'

const props = defineProps<{
  visible: boolean
  report?: any | null
  initialDraft?: any | null
  overlayClass?: string
  overlayStyle?: Record<string, string>
  scrollbarVariant?: 'embed' | 'debug'
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', report: any): void
}>()

const { showToast } = useToast()

const activeReport = computed(() => props.report || props.initialDraft || null)

type ReportSourceType = 'connection' | 'dataset'

const createEmptyForm = () => ({
  title: '',
  description: '',
  tags: '',
  sourceType: 'connection' as ReportSourceType,
  connectionId: null as number | null,
  datasetId: null as number | null,
  sqlContent: 'SELECT * FROM \nLIMIT 50',
})

// 表单状态
const form = ref(createEmptyForm())

type CustomParameterType = 'text' | 'number' | 'select'
type CustomParameterConfig = {
  name: string
  label: string
  type: CustomParameterType
  required: boolean
  defaultValue: string
  optionsText: string
}

const customParameterConfigs = ref<CustomParameterConfig[]>([])

const sqlEditorHost = ref<HTMLDivElement | null>(null)
const sqlEditorView = shallowRef<EditorView | null>(null)
const sqlEditorSyncing = ref(false)
const showSqlHelp = ref(false)

const sqlParameterShortcuts = [
  { label: '开始日期', insert: '{{start_date}}' },
  { label: '结束日期', insert: '{{end_date}}' },
  { label: '开始时间', insert: '{{start_datetime}}' },
  { label: '结束时间', insert: '{{end_datetime}}' },
  { label: '开始月份', insert: '{{start_month}}' },
  { label: '结束月份', insert: '{{end_month}}' },
  {
    label: '日期条件片段',
    insert: 'order_date >= {{start_date}}\nAND order_date < {{end_date}}',
  },
  {
    label: '月份条件片段',
    insert: 'order_month BETWEEN {{start_month}} AND {{end_month}}',
  },
  { label: '插入自定义参数', insert: '{{custom_param}}' },
]

const dateParameterNames = new Set(['start_date', 'end_date', 'start_datetime', 'end_datetime'])
const monthParameterNames = new Set(['start_month', 'end_month'])
const supportedParameterNames = new Set([...dateParameterNames, ...monthParameterNames])
const sqlParameterPattern = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g

const extractSqlParameterNames = (sql: string) => {
  const names = new Set<string>()
  for (const match of sql.matchAll(sqlParameterPattern)) {
    const name = match[1]?.trim()
    if (name) names.add(name)
  }
  return Array.from(names)
}

const validateSqlParameters = (sql: string) => {
  const names = extractSqlParameterNames(sql)
  const hasDateParameters = names.some((name) => dateParameterNames.has(name))
  const hasMonthParameters = names.some((name) => monthParameterNames.has(name))
  if (hasDateParameters && hasMonthParameters) {
    return '当前一张固化报表请只使用日期范围或月份范围中的一种动态参数。'
  }
  return ''
}

const customParameterNames = (sql: string) =>
  extractSqlParameterNames(sql).filter((name) => !supportedParameterNames.has(name))

const syncCustomParameterConfigs = (sql: string) => {
  const existing = new Map(customParameterConfigs.value.map((item) => [item.name, item]))
  customParameterConfigs.value = customParameterNames(sql).map((name) => existing.get(name) || ({
    name,
    label: name,
    type: 'text',
    required: true,
    defaultValue: '',
    optionsText: '',
  }))
}

const customParameterOptions = (config: CustomParameterConfig) =>
  config.optionsText.split(/[,，\n]+/).map((item) => item.trim()).filter(Boolean)

const validateCustomParameterConfigs = () => {
  for (const config of customParameterConfigs.value) {
    if (!config.label.trim()) return `请填写参数「${config.name}」的显示名称`
    if (config.type === 'select') {
      const options = customParameterOptions(config)
      if (!options.length) return `请为下拉参数「${config.name}」填写候选值`
      if (config.required && !config.defaultValue.trim()) return `请为必填参数「${config.name}」设置默认值`
      if (config.defaultValue.trim() && !options.includes(config.defaultValue.trim())) {
        return `参数「${config.name}」的默认值必须在候选值中`
      }
    }
    if (config.type === 'number' && config.defaultValue.trim() && !Number.isFinite(Number(config.defaultValue))) {
      return `参数「${config.name}」的默认值必须是数字`
    }
    if (config.required && !config.defaultValue.trim()) return `请为必填参数「${config.name}」设置默认值`
  }
  return ''
}

const buildParameterSchema = (sql: string) => {
  const names = extractSqlParameterNames(sql)
  const schema: Array<Record<string, any>> = []
  if (names.some((name) => dateParameterNames.has(name))) {
    schema.push({
      name: 'date_range',
      type: 'date_range',
      label: '日期范围',
      default: 'month_start_to_today',
      options: ['today', 'yesterday', 'last_7_days', 'month_start_to_today', 'year_start_to_today', 'custom_range'],
    })
  } else if (names.some((name) => monthParameterNames.has(name))) {
    schema.push({
      name: 'month_range',
      type: 'month_range',
      label: '月份范围',
      default: 'last_6_completed_months',
      options: ['last_6_completed_months', 'year_start_to_current_month', 'custom_month_range'],
    })
  }
  customParameterConfigs.value.forEach((config) => {
    schema.push({
      name: config.name,
      type: config.type,
      label: config.label.trim(),
      required: config.required,
      default: config.type === 'number' && config.defaultValue.trim()
        ? Number(config.defaultValue)
        : config.defaultValue.trim() || undefined,
      ...(config.type === 'select' ? { options: customParameterOptions(config) } : {}),
    })
  })
  return schema
}

const buildDefaultParams = (parameterSchema: Array<{ name: string; default?: any }>) => {
  const defaults: Record<string, any> = {}
  for (const item of parameterSchema) {
    if (item.default !== undefined) defaults[item.name] = item.default
  }
  return defaults
}

const padNumber = (value: number) => String(value).padStart(2, '0')

const formatDateValue = (value: Date) => (
  `${value.getFullYear()}-${padNumber(value.getMonth() + 1)}-${padNumber(value.getDate())}`
)

const formatMonthValue = (value: Date) => (
  `${value.getFullYear()}-${padNumber(value.getMonth() + 1)}`
)

const buildDefaultTestParameterForm = (): TestParameterForm => {
  const today = new Date()
  const tomorrow = new Date(today)
  tomorrow.setDate(today.getDate() + 1)
  const previousMonth = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  const sixMonthsBeforePrevious = new Date(today.getFullYear(), today.getMonth() - 6, 1)
  const customParams: Record<string, any> = {}
  for (const config of customParameterConfigs.value) {
    const existingValue = testParameterForm.value.customParams?.[config.name]
    customParams[config.name] = existingValue !== undefined
      ? existingValue
      : config.defaultValue.trim()
  }
  return {
    dateRange: testParameterForm.value.dateRange || 'month_start_to_today',
    startDate: testParameterForm.value.startDate || formatDateValue(new Date(today.getFullYear(), today.getMonth(), 1)),
    endDate: testParameterForm.value.endDate || formatDateValue(tomorrow),
    monthRange: testParameterForm.value.monthRange || 'last_6_completed_months',
    startMonth: testParameterForm.value.startMonth || formatMonthValue(sixMonthsBeforePrevious),
    endMonth: testParameterForm.value.endMonth || formatMonthValue(previousMonth),
    customParams,
  }
}

const openTestParameterModal = () => {
  syncCustomParameterConfigs(form.value.sqlContent)
  testParameterForm.value = buildDefaultTestParameterForm()
  testParameterError.value = ''
  showTestParameterModal.value = true
}

const closeTestParameterModal = () => {
  showTestParameterModal.value = false
  testParameterError.value = ''
}

const parseDateInput = (value: string) => {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return null
  const parsed = new Date(year, month - 1, day)
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day
    ? parsed
    : null
}

const parseMonthInput = (value: string) => {
  const [year, month] = value.split('-').map(Number)
  if (!year || !month || month < 1 || month > 12) return null
  return new Date(year, month - 1, 1)
}

const resolveTestDateRange = (parameterForm = testParameterForm.value) => {
  const today = new Date()
  const dateRange = parameterForm.dateRange || 'month_start_to_today'
  let start: Date
  let end: Date
  let endInclusive: Date
  if (dateRange === 'today') {
    start = today
    end = new Date(today)
    end.setDate(today.getDate() + 1)
    endInclusive = today
  } else if (dateRange === 'yesterday') {
    start = new Date(today)
    start.setDate(today.getDate() - 1)
    end = today
    endInclusive = start
  } else if (dateRange === 'last_7_days') {
    start = new Date(today)
    start.setDate(today.getDate() - 6)
    end = new Date(today)
    end.setDate(today.getDate() + 1)
    endInclusive = today
  } else if (dateRange === 'month_start_to_today') {
    start = new Date(today.getFullYear(), today.getMonth(), 1)
    end = new Date(today)
    end.setDate(today.getDate() + 1)
    endInclusive = today
  } else if (dateRange === 'year_start_to_today') {
    start = new Date(today.getFullYear(), 0, 1)
    end = new Date(today)
    end.setDate(today.getDate() + 1)
    endInclusive = today
  } else if (dateRange === 'custom_range') {
    const customStart = parseDateInput(parameterForm.startDate)
    const customEnd = parseDateInput(parameterForm.endDate)
    if (!customStart || !customEnd) return { values: {}, error: '请选择有效的开始日期和结束日期' }
    if (customEnd <= customStart) return { values: {}, error: '结束日期必须晚于开始日期' }
    start = customStart
    end = customEnd
    endInclusive = customEnd
  } else {
    return { values: {}, error: '不支持的日期范围' }
  }
  return {
    values: {
      start_date: formatDateValue(start),
      end_date: formatDateValue(end),
      start_datetime: `${formatDateValue(start)} 00:00:00`,
      end_datetime: `${formatDateValue(endInclusive)} 23:59:59`,
    },
    error: '',
  }
}

const resolveTestMonthRange = (parameterForm = testParameterForm.value) => {
  const today = new Date()
  const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1)
  let start: Date
  let end: Date
  const monthRange = parameterForm.monthRange || 'last_6_completed_months'
  if (monthRange === 'last_6_completed_months') {
    end = new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1)
    start = new Date(end.getFullYear(), end.getMonth() - 5, 1)
  } else if (monthRange === 'year_start_to_current_month') {
    start = new Date(today.getFullYear(), 0, 1)
    end = currentMonth
  } else if (monthRange === 'custom_month_range') {
    const customStart = parseMonthInput(parameterForm.startMonth)
    const customEnd = parseMonthInput(parameterForm.endMonth)
    if (!customStart || !customEnd) return { values: {}, error: '请选择有效的开始月份和结束月份' }
    if (customEnd < customStart) return { values: {}, error: '结束月份不能早于开始月份' }
    start = customStart
    end = customEnd
  } else {
    return { values: {}, error: '不支持的月份范围' }
  }
  return {
    values: {
      start_month: formatMonthValue(start),
      end_month: formatMonthValue(end),
    },
    error: '',
  }
}

const renderTestCustomParameter = (config: CustomParameterConfig, parameterForm = testParameterForm.value) => {
  const rawValue = parameterForm.customParams?.[config.name]
  const value = rawValue == null ? '' : String(rawValue).trim()
  if (!value) {
    if (config.required) return { value: '', error: `请填写参数「${config.label || config.name}」` }
    return { value: 'NULL', error: '' }
  }
  if (config.type === 'number') {
    if (!Number.isFinite(Number(value))) return { value: '', error: `参数「${config.label || config.name}」必须是数字` }
    return { value, error: '' }
  }
  if (config.type === 'select') {
    const options = customParameterOptions(config)
    if (!options.includes(value)) return { value: '', error: `参数「${config.label || config.name}」不在候选值中` }
  }
  return { value: `'${value.replace(/'/g, "''")}'`, error: '' }
}

const buildPreviewSql = (sql: string, parameterForm = testParameterForm.value) => {
  const validationError = validateSqlParameters(sql)
  if (validationError) return { sql: '', error: validationError }
  const customValidationError = validateCustomParameterConfigs()
  if (customValidationError) return { sql: '', error: customValidationError }

  const values: Record<string, string> = {}
  if (hasTestDateParameters.value) {
    const dateResult = resolveTestDateRange(parameterForm)
    if (dateResult.error) return { sql: '', error: dateResult.error }
    Object.assign(values, dateResult.values)
  }
  if (hasTestMonthParameters.value) {
    const monthResult = resolveTestMonthRange(parameterForm)
    if (monthResult.error) return { sql: '', error: monthResult.error }
    Object.assign(values, monthResult.values)
  }

  const customValues = new Map(customParameterConfigs.value.map((config) => [config.name, config]))
  let customParameterError = ''
  const renderedSql = sql.replace(sqlParameterPattern, (_match, name: string) => {
    const normalizedName = name.trim()
    const value = values[normalizedName]
    if (value != null) return `'${value}'`
    const config = customValues.get(normalizedName)
    if (!config) return _match
    const rendered = renderTestCustomParameter(config, parameterForm)
    if (rendered.error) customParameterError = rendered.error
    return rendered.value
  })
  if (customParameterError) return { sql: '', error: customParameterError }
  return { sql: renderedSql, error: '' }
}

const sqlEditorTheme = EditorView.theme({
  '&': {
    backgroundColor: '#020617',
    color: '#34d399',
    border: '1px solid rgb(51 65 85)',
    borderRadius: '0.75rem',
    fontSize: '0.75rem',
  },
  '&.cm-focused': {
    outline: '2px solid rgb(59 130 246 / 0.35)',
    outlineOffset: '1px',
    borderColor: 'rgb(59 130 246)',
  },
  '.cm-scroller': {
    minHeight: '9rem',
    maxHeight: '18rem',
    overflow: 'auto',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.625',
  },
  '.cm-content': {
    minHeight: '9rem',
    padding: '0.625rem 0.875rem',
    caretColor: '#60a5fa',
  },
  '.cm-line': {
    padding: '0',
  },
  '.cm-gutters': {
    backgroundColor: '#020617',
    color: '#64748b',
    borderRight: '1px solid rgb(30 41 59)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgb(15 23 42 / 0.75)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'rgb(15 23 42)',
    color: '#cbd5e1',
  },
  '.cm-selectionBackground, ::selection': {
    backgroundColor: 'rgb(37 99 235 / 0.35) !important',
  },
})

const destroySqlEditor = () => {
  sqlEditorView.value?.destroy()
  sqlEditorView.value = null
}

const createSqlEditor = () => {
  const host = sqlEditorHost.value
  if (!host) return
  destroySqlEditor()

  const updateListener = EditorView.updateListener.of((update) => {
    if (!update.docChanged || sqlEditorSyncing.value) return
    const nextSql = update.state.doc.toString()
    if (form.value.sqlContent !== nextSql) form.value.sqlContent = nextSql
  })
  const state = EditorState.create({
    doc: form.value.sqlContent,
    extensions: [
      basicSetup,
      sql(),
      placeholder('SELECT id, department_name, sum(amount) AS total_sales FROM sales_table GROUP BY id, department_name LIMIT 50;'),
      sqlEditorTheme,
      updateListener,
    ],
  })
  sqlEditorView.value = new EditorView({ state, parent: host })
}

const syncSqlEditorDocument = (sqlContent: string) => {
  const view = sqlEditorView.value
  if (!view || view.state.doc.toString() === sqlContent) return
  sqlEditorSyncing.value = true
  try {
    view.dispatch({
      changes: {
        from: 0,
        to: view.state.doc.length,
        insert: sqlContent,
      },
    })
  } finally {
    sqlEditorSyncing.value = false
  }
}

const insertSqlFragment = (fragment: string) => {
  const currentSql = form.value.sqlContent
  const view = sqlEditorView.value
  const selection = view?.state.selection.main
  const start = selection?.from ?? currentSql.length
  const end = selection?.to ?? start
  const nextSql = `${currentSql.slice(0, start)}${fragment}${currentSql.slice(end)}`
  const validationError = validateSqlParameters(nextSql)
  if (validationError) {
    showToast(validationError, 'warning')
    return
  }

  if (view) {
    const nextCursor = start + fragment.length
    view.dispatch({
      changes: { from: start, to: end, insert: fragment },
      selection: { anchor: nextCursor },
    })
    view.focus()
    return
  }

  form.value.sqlContent = nextSql
}

const formatDatasetLabel = (dataset: { id: number; name: string; display_name?: string }) => {
  const physicalName = String(dataset.name || '').trim()
  const displayName = String(dataset.display_name || '').trim()
  const namePart = displayName && displayName !== physicalName
    ? `${displayName}（${physicalName}）`
    : physicalName
  return `#${dataset.id} - ${namePart}`
}

// 数据源与数据集列表
const loadingSources = ref(false)
const dbConnections = ref<Array<{ id: number; name: string; source_key?: string; db_type: string; database_name: string }>>([])
const datasets = ref<Array<{ id: number; name: string; display_name?: string; data_source?: string; description?: string }>>([])
const sourceError = ref('')
const pendingSourceName = ref('')
const pendingDatasetName = ref('')

// 试跑状态与结果
const testing = ref(false)
const testPassed = ref(false)
const testError = ref('')
const testResult = ref<{
  columns: Array<{ name: string; type?: string }>
  rows: any[][]
  execution_time_ms?: number
} | null>(null)

type TestParameterForm = {
  dateRange: string
  startDate: string
  endDate: string
  monthRange: string
  startMonth: string
  endMonth: string
  customParams: Record<string, any>
}

const showTestParameterModal = ref(false)
const testParameterError = ref('')
const testParameterForm = ref<TestParameterForm>({
  dateRange: 'month_start_to_today',
  startDate: '',
  endDate: '',
  monthRange: 'last_6_completed_months',
  startMonth: '',
  endMonth: '',
  customParams: {},
})

const testSqlParameterNames = computed(() => extractSqlParameterNames(form.value.sqlContent))
const hasTestDateParameters = computed(() => testSqlParameterNames.value.some((name) => dateParameterNames.has(name)))
const hasTestMonthParameters = computed(() => testSqlParameterNames.value.some((name) => monthParameterNames.has(name)))
const hasTestParameters = computed(() => testSqlParameterNames.value.length > 0)

// 保存状态
const saving = ref(false)

// 当前选中的数据源标识
const selectedDataSourceName = computed(() => {
  if (form.value.sourceType === 'connection' && form.value.connectionId) {
    const conn = dbConnections.value.find((c) => c.id === form.value.connectionId)
    return conn ? (conn.source_key || conn.name) : pendingSourceName.value
  }
  if (form.value.sourceType === 'dataset' && form.value.datasetId) {
    const dataset = datasets.value.find((item) => item.id === form.value.datasetId)
    return dataset?.data_source || 'default_clickhouse'
  }
  return ''
})

const loadDataSourcesAndDatasets = async () => {
  loadingSources.value = true
  sourceError.value = ''
  try {
    const [connResult, dsResult] = await Promise.allSettled([
      axios.get('/api/portal/saved-reports/source-options'),
      axios.get('/api/portal/metadata/datasets/accessible'),
    ])

    const sourceErrors: string[] = []
    const isAiDraft = Boolean(activeReport.value?.original_query)
    if (connResult.status === 'fulfilled') {
      dbConnections.value = Array.isArray(connResult.value.data?.data) ? connResult.value.data.data : []
    } else {
      sourceErrors.push(connResult.reason?.response?.status === 403 ? '当前账号无物理数据源读取权限' : '物理数据源加载失败')
      dbConnections.value = []
    }
    if (form.value.sourceType === 'connection') {
      let sourceResolutionMessage = ''
      if (pendingSourceName.value) {
        const normalizedPendingSource = pendingSourceName.value.trim().toLowerCase()
        const matched = dbConnections.value.find((item) =>
          [item.source_key, item.name]
            .filter(Boolean)
            .some((value) => String(value).trim().toLowerCase() === normalizedPendingSource),
        )
        form.value.connectionId = matched?.id ?? null
        if (!matched) {
          sourceResolutionMessage = `原查询数据源不可用（${pendingSourceName.value}），请手动确认关联数据源。`
        }
      }
      if (!pendingSourceName.value && !isAiDraft && dbConnections.value.length > 0 && !form.value.connectionId) {
        form.value.connectionId = dbConnections.value[0]?.id ?? null
      }
      if (isAiDraft && !pendingSourceName.value) {
        sourceResolutionMessage = '未能从本次 AI 查询识别数据源，请手动确认关联数据源。'
      }
      if (sourceResolutionMessage) sourceError.value = sourceResolutionMessage
    }

    if (dsResult.status === 'fulfilled') {
      datasets.value = Array.isArray(dsResult.value.data) ? dsResult.value.data : (dsResult.value.data?.data || [])
    } else {
      sourceErrors.push(dsResult.reason?.response?.status === 403 ? '当前账号无可访问数据集' : '数据集加载失败')
      datasets.value = []
    }

    let datasetResolutionMessage = ''
    if (pendingDatasetName.value) {
      const normalizedPendingDataset = pendingDatasetName.value.trim().toLowerCase()
      const matchedDatasets = datasets.value.filter((item) =>
        [item.name, item.display_name]
          .filter(Boolean)
          .some((value) => String(value).trim().toLowerCase() === normalizedPendingDataset),
      )
      const matchedDataset = matchedDatasets[0]
      if (matchedDataset) {
        form.value.sourceType = 'dataset'
        form.value.datasetId = matchedDataset.id
        sourceError.value = ''
      } else if (matchedDatasets.length === 0) {
        form.value.sourceType = 'dataset'
        form.value.datasetId = null
        datasetResolutionMessage = `原查询数据集不可用（${pendingDatasetName.value}），请手动确认关联数据集。`
      } else {
        form.value.sourceType = 'dataset'
        form.value.datasetId = null
        datasetResolutionMessage = `原查询数据集存在多个匹配项（${pendingDatasetName.value}），请手动确认关联数据集。`
      }
    }

    if (form.value.sourceType === 'dataset') {
      const selectedDatasetAvailable = Number(form.value.datasetId) > 0
        && datasets.value.some((item) => Number(item.id) === Number(form.value.datasetId))
      if (selectedDatasetAvailable) {
        sourceError.value = ''
      } else if (datasetResolutionMessage) {
        sourceError.value = datasetResolutionMessage
      } else if (isAiDraft && !pendingDatasetName.value) {
        sourceError.value = '未能从本次 AI 查询识别数据集，请手动确认关联数据集。'
      }
    }

    if (!dbConnections.value.length && datasets.value.length && !pendingSourceName.value && !pendingDatasetName.value) {
      form.value.sourceType = 'dataset'
      if (isAiDraft && !form.value.datasetId) {
        sourceError.value = '未能从本次 AI 查询识别数据集，请手动确认关联数据集。'
      } else if (!isAiDraft) {
        sourceError.value = ''
      }
    }
    if (!dbConnections.value.length && !datasets.value.length) {
      sourceError.value = sourceErrors.length
        ? `${sourceErrors.join('；')}，请联系管理员确认访问权限。`
        : '当前账号暂无可用数据源或数据集，请联系管理员配置访问权限。'
    }
  } catch (err: any) {
    sourceError.value = err.response?.data?.detail || '数据源加载失败，请稍后重试。'
  } finally {
    loadingSources.value = false
  }
}

const resetForm = (report?: any | null) => {
  const hasDatasetContext = Boolean(report?.dataset_id || report?.dataset_name)
  pendingSourceName.value = hasDatasetContext ? '' : String(report?.data_source || '')
  pendingDatasetName.value = report?.dataset_id ? '' : String(report?.dataset_name || '')
  form.value = {
    title: report?.title || '',
    description: report?.description || '',
    tags: Array.isArray(report?.tags) ? report.tags.join(', ') : String(report?.tags_input || ''),
    sourceType: hasDatasetContext ? 'dataset' : 'connection',
    connectionId: null,
    datasetId: report?.dataset_id ?? null,
    sqlContent: report?.sql_template || report?.sql_content || 'SELECT * FROM \nLIMIT 50',
  }
  customParameterConfigs.value = Array.isArray(report?.params_schema)
    ? report.params_schema
      .filter((item: any) => ['text', 'number', 'select'].includes(String(item?.type || '')))
      .map((item: any) => ({
        name: String(item.name),
        label: String(item.label || item.name),
        type: (item.type || 'text') as CustomParameterType,
        required: item.required !== false,
        defaultValue: String(report?.default_params?.[item.name] ?? item.default ?? ''),
        optionsText: Array.isArray(item.options) ? item.options.join(', ') : '',
      }))
    : []
  syncCustomParameterConfigs(form.value.sqlContent)
  testPassed.value = false
  testError.value = ''
  testResult.value = null
  sourceError.value = ''
  showSqlHelp.value = false
  showTestParameterModal.value = false
  testParameterError.value = ''
  testParameterForm.value = {
    dateRange: 'month_start_to_today',
    startDate: '',
    endDate: '',
    monthRange: 'last_6_completed_months',
    startMonth: '',
    endMonth: '',
    customParams: {},
  }
}

// 试跑 SQL
const executeTestSql = async () => {
  const previewSql = buildPreviewSql(form.value.sqlContent.trim())
  if (previewSql.error) {
    testError.value = previewSql.error
    if (showTestParameterModal.value) {
      testParameterError.value = previewSql.error
    } else {
      showToast(previewSql.error, 'warning')
    }
    return
  }

  if (form.value.sourceType === 'connection' && !form.value.connectionId) {
    const message = '请选择数据源连接'
    if (showTestParameterModal.value) testParameterError.value = message
    else showToast(message, 'warning')
    return
  }
  if (form.value.sourceType === 'dataset' && !form.value.datasetId) {
    const message = '请选择所属数据集'
    if (showTestParameterModal.value) testParameterError.value = message
    else showToast(message, 'warning')
    return
  }

  showTestParameterModal.value = false
  testParameterError.value = ''
  testing.value = true
  testError.value = ''
  testResult.value = null
  testPassed.value = false

  try {
    const res = await axios.post('/api/portal/saved-reports/preview-sql', {
      sql: previewSql.sql,
      source_type: form.value.sourceType,
      connection_id: form.value.sourceType === 'connection' ? form.value.connectionId : undefined,
      dataset_id: form.value.sourceType === 'dataset' ? form.value.datasetId : undefined,
      limit: 50,
    })
      if (res.data?.status === 'success' || res.data?.data) {
        testResult.value = res.data.data || res.data
        testPassed.value = true
        showToast('SQL 试跑成功，已返回真实数据预览', 'success')
      } else {
        testError.value = res.data?.message || '执行未返回有效数据'
      }
  } catch (err: any) {
    const detail = err.response?.data?.detail || err.message || 'SQL 执行失败'
    testError.value = typeof detail === 'string' ? detail : JSON.stringify(detail)
    testPassed.value = false
  } finally {
    testing.value = false
  }
}

const runTestSql = async () => {
  if (!form.value.sqlContent.trim()) {
    showToast('请输入 SELECT SQL 语句', 'warning')
    return
  }
  if (hasTestParameters.value) {
    openTestParameterModal()
    return
  }
  await executeTestSql()
}

// 提交保存固化报表
const handleSubmit = async () => {
  if (!form.value.title.trim()) {
    showToast('请输入报表名称', 'warning')
    return
  }
  if (!form.value.sqlContent.trim()) {
    showToast('请输入 SQL 语句', 'warning')
    return
  }
  const sqlContent = form.value.sqlContent.trim()
  const parameterError = validateSqlParameters(sqlContent)
  if (parameterError) {
    showToast(parameterError, 'warning')
    return
  }
  const customParameterError = validateCustomParameterConfigs()
  if (customParameterError) {
    showToast(customParameterError, 'warning')
    return
  }
  if (!testPassed.value) {
    showToast('请先试跑 SQL，确认结果后再保存', 'warning')
    return
  }
  if (!selectedDataSourceName.value) {
    showToast('请选择有效的数据源或数据集', 'warning')
    return
  }

  saving.value = true
  try {
    const tagList = form.value.tags
      .split(/[,，\s]+/)
      .map((t) => t.trim())
      .filter(Boolean)
    const currentParameterNames = extractSqlParameterNames(sqlContent).sort().join(',')
    const existingParameterNames = extractSqlParameterNames(String(activeReport.value?.sql_template || '')).sort().join(',')
    const preserveExistingParameterConfig = Boolean(
      activeReport.value?.id
      && activeReport.value?.sql_template
      && Array.isArray(activeReport.value?.params_schema)
      && activeReport.value.params_schema.length
      && currentParameterNames === existingParameterNames,
    )
    const parameterSchema = preserveExistingParameterConfig
      ? activeReport.value.params_schema
      : buildParameterSchema(sqlContent)
    const defaultParams = preserveExistingParameterConfig
      ? (activeReport.value?.default_params || {})
      : buildDefaultParams(parameterSchema)

    const payload: any = {
      title: form.value.title.trim(),
      description: form.value.description.trim() || undefined,
      sql_content: sqlContent,
      data_source: selectedDataSourceName.value,
      dataset_id: form.value.sourceType === 'dataset' ? form.value.datasetId : null,
      tags: tagList,
      mode: parameterSchema.length ? 'param_sql' : 'static_sql',
      sql_template: parameterSchema.length ? sqlContent : undefined,
      params_schema: parameterSchema,
      default_params: defaultParams,
      original_query: activeReport.value?.original_query || undefined,
      column_meta: activeReport.value?.column_meta || undefined,
      analysis_mode: activeReport.value?.analysis_mode || undefined,
    }

    const res = activeReport.value?.id
      ? await axios.put(`/api/portal/saved-reports/${activeReport.value.id}`, payload)
      : await axios.post('/api/portal/saved-reports', payload)
    if (res.data?.status === 'success' || res.data?.data) {
      showToast(activeReport.value?.id ? '固化报表已更新' : '固化报表创建成功！已录入报表库', 'success')
      emit('created', res.data.data || res.data)
      emit('close')
    } else {
      showToast(res.data?.message || '保存失败', 'error')
    }
  } catch (err: any) {
    const detail = err.response?.data?.detail || err.message || '创建固化报表失败'
    showToast(typeof detail === 'string' ? detail : '创建失败', 'error')
  } finally {
    saving.value = false
  }
}

watch(
  () => [form.value.sqlContent, form.value.sourceType, form.value.connectionId, form.value.datasetId, JSON.stringify(customParameterConfigs.value)],
  () => {
    testPassed.value = false
    testResult.value = null
    testError.value = ''
  },
)

watch(
  () => form.value.sqlContent,
  (sql) => {
    syncCustomParameterConfigs(sql)
    syncSqlEditorDocument(sql)
  },
)

watch(
  () => props.visible,
  (visible) => {
    if (!visible) {
      destroySqlEditor()
      return
    }
    resetForm(activeReport.value)
    loadDataSourcesAndDatasets()
    void nextTick(createSqlEditor)
  },
)

onMounted(() => {
  if (props.visible) {
    resetForm(activeReport.value)
    loadDataSourcesAndDatasets()
    void nextTick(createSqlEditor)
  }
})

onBeforeUnmount(destroySqlEditor)
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[250] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in"
    :class="overlayClass"
    :style="overlayStyle"
    @click.self="emit('close')"
  >
    <div
      class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden border border-gray-100 dark:border-gray-800"
    >
      <!-- Modal Header -->
      <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-800 flex justify-between items-center bg-gradient-to-r from-blue-50/60 to-indigo-50/40 dark:from-gray-800/80 dark:to-gray-800/40">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center shadow-md shadow-blue-500/20">
            <DocumentPlusIcon class="w-6 h-6" />
          </div>
          <div>
            <h3 class="text-base font-bold text-gray-900 dark:text-gray-100">
              {{ activeReport?.id ? '编辑固化报表' : '新建固化报表' }}
            </h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              手写或粘贴自定义 SELECT SQL，在线试跑验证后保存为标准化固化报表
            </p>
          </div>
        </div>
        <button
          type="button"
          class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors cursor-pointer"
          @click="emit('close')"
        >
          <XMarkIcon class="w-6 h-6" />
        </button>
      </div>

      <!-- Modal Body -->
      <div
        class="flex-1 overflow-y-auto p-6 space-y-5 custom-scrollbar"
        :class="{ 'custom-scrollbar-embed': scrollbarVariant === 'embed' }"
      >
        <!-- 基础信息行 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-bold text-gray-700 dark:text-gray-300 mb-1">
              报表名称 <span class="text-red-500">*</span>
            </label>
            <input
              v-model="form.title"
              type="text"
              placeholder="例如：2026年各部门月度营收汇总"
              class="w-full px-3.5 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-950 focus:bg-white dark:focus:bg-gray-900 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-gray-900 dark:text-gray-100 disabled:bg-gray-100"
            />
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-700 dark:text-gray-300 mb-1">
              业务标签 (Tags)
            </label>
            <input
              v-model="form.tags"
              type="text"
              placeholder="多个标签用逗号分隔，例如：财务, 订单, 月报"
              class="w-full px-3.5 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-950 focus:bg-white dark:focus:bg-gray-900 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-gray-900 dark:text-gray-100 disabled:bg-gray-100"
            />
          </div>
        </div>

        <!-- 关联数据源 -->
        <div class="p-4 rounded-xl border border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-950/40 space-y-3">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-gray-800 dark:text-gray-200 flex items-center gap-1.5">
              <CircleStackIcon class="w-4 h-4 text-blue-600" />
              选择关联数据源
            </span>
            <div class="flex items-center gap-2 text-xs">
              <label class="inline-flex items-center gap-1 cursor-pointer">
                <input
                  v-model="form.sourceType"
                  type="radio"
                  value="connection"
                  class="text-blue-600 focus:ring-blue-500"
                />
                <span>物理数据源</span>
              </label>
              <label class="inline-flex items-center gap-1 cursor-pointer">
                <input
                  v-model="form.sourceType"
                  type="radio"
                  value="dataset"
                  class="text-blue-600 focus:ring-blue-500"
                />
                <span>元数据数据集</span>
              </label>
            </div>
          </div>

          <div v-if="form.sourceType === 'connection'" class="flex items-center gap-2">
            <select
              v-model="form.connectionId"
              class="flex-1 px-3 py-2 text-xs font-medium border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-blue-500/20 outline-none"
            >
              <option :value="null" disabled>请选择数据源连接</option>
              <option v-for="conn in dbConnections" :key="conn.id" :value="conn.id">
                [{{ conn.db_type.toUpperCase() }}] {{ conn.name }} ({{ conn.database_name }})
              </option>
            </select>
            <button
              type="button"
              class="p-2 text-gray-500 hover:text-blue-600 hover:bg-gray-100 rounded-lg transition-colors"
              title="刷新数据源列表"
              @click="loadDataSourcesAndDatasets"
            >
              <ArrowPathIcon class="w-4 h-4" :class="{ 'animate-spin': loadingSources }" />
            </button>
          </div>

          <div v-else class="flex items-center gap-2">
            <select
              v-model="form.datasetId"
              class="flex-1 px-3 py-2 text-xs font-medium border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-blue-500/20 outline-none"
            >
              <option :value="null" disabled>请选择所属数据集</option>
              <option v-for="ds in datasets" :key="ds.id" :value="ds.id">
                {{ formatDatasetLabel(ds) }}
              </option>
            </select>
          </div>
          <p v-if="sourceError" class="text-[11px] text-amber-600 dark:text-amber-300">
            {{ sourceError }}
          </p>
        </div>

        <!-- AI 来源上下文：只读展示，不参与 SQL 执行 -->
        <div v-if="activeReport?.original_query">
          <label class="block text-xs font-bold text-gray-700 dark:text-gray-300 mb-1">
            来源提问
          </label>
          <div class="w-full px-3.5 py-2 text-sm border border-blue-100 dark:border-blue-900/40 rounded-xl bg-blue-50/50 dark:bg-blue-950/20 text-gray-600 dark:text-gray-300 break-words leading-relaxed">
            {{ activeReport.original_query }}
          </div>
        </div>

        <!-- 报表描述 -->
        <div>
          <label class="block text-xs font-bold text-gray-700 dark:text-gray-300 mb-1">
            报表描述与业务口径说明
          </label>
          <textarea
            v-model="form.description"
            rows="2"
            placeholder="说明本报表的统计维度、过滤条件及核心业务口径..."
            class="w-full px-3.5 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-950 focus:bg-white dark:focus:bg-gray-900 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-gray-900 dark:text-gray-100 resize-none disabled:bg-gray-100"
          ></textarea>
        </div>

        <!-- SQL 编写区与试跑控制栏 -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-1.5">
              <label class="text-xs font-bold text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <span>自定义 SELECT SQL 语句</span>
              <span class="text-red-500">*</span>
              <span class="text-[10px] text-gray-400 font-normal">（仅允许只读查询，请避免 DDL/DML）</span>
              </label>
              <button
                type="button"
                class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-blue-300 text-[10px] font-bold text-blue-600 hover:bg-blue-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950"
                aria-label="查看动态参数 SQL 写法说明"
                title="查看动态参数 SQL 写法说明"
                @click="showSqlHelp = !showSqlHelp"
              >
                ?
              </button>
            </div>

            <!-- 试跑按钮 -->
            <button
              type="button"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold shadow-xs transition-all cursor-pointer disabled:opacity-50"
              :disabled="testing || !form.sqlContent.trim()"
              @click="runTestSql"
            >
              <PlayIcon class="w-3.5 h-3.5" :class="{ 'animate-spin': testing }" />
              <span>{{ testing ? '正在试跑...' : '▶ 试跑测试 SQL' }}</span>
            </button>
          </div>

          <div
            v-if="showSqlHelp"
            class="rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-[11px] leading-5 text-blue-900 dark:border-blue-900/50 dark:bg-blue-950/30 dark:text-blue-100"
          >
            <p class="font-bold">动态参数说明</p>
            <p>快捷按钮会在光标位置插入占位符，参数不要再额外套单引号；试跑时系统会先用默认日期或月份替换后再执行。</p>
            <p>
              日期范围：<code v-pre>{{start_date}}</code> / <code v-pre>{{end_date}}</code>；日期时间：<code v-pre>{{start_datetime}}</code> / <code v-pre>{{end_datetime}}</code>；月份范围：<code v-pre>{{start_month}}</code> / <code v-pre>{{end_month}}</code>。
            </p>
            <p>保存后运行报表时可选择日期、月份，或填写自定义文本、数字、下拉参数。参数占位符不要额外套单引号，系统会按参数类型安全转义。</p>
          </div>

          <div class="flex flex-wrap items-center gap-1.5">
            <span class="mr-1 text-[10px] font-bold text-gray-400">快捷插入</span>
            <button
              v-for="shortcut in sqlParameterShortcuts"
              :key="shortcut.label"
              type="button"
              class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[10px] font-medium text-gray-600 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-blue-700 dark:hover:bg-blue-950 dark:hover:text-blue-200"
              :title="`插入 ${shortcut.insert}`"
              @click="insertSqlFragment(shortcut.insert)"
            >
              {{ shortcut.label }}
            </button>
          </div>

          <div
            ref="sqlEditorHost"
            class="sql-editor-host w-full overflow-hidden rounded-xl"
            aria-label="SQL 编辑器"
          ></div>
          <textarea
            v-model="form.sqlContent"
            class="sr-only"
            aria-hidden="true"
            tabindex="-1"
            aria-label="SQL 内容同步字段"
          ></textarea>

          <div
            v-if="customParameterConfigs.length"
            class="space-y-3 rounded-xl border border-violet-100 bg-violet-50/50 p-3 dark:border-violet-900/40 dark:bg-violet-950/20"
          >
            <div class="flex items-center justify-between gap-2">
              <div>
                <p class="text-xs font-bold text-violet-900 dark:text-violet-200">自定义参数配置</p>
                <p class="mt-0.5 text-[10px] text-violet-700/80 dark:text-violet-300/80">运行报表时会显示这些参数；试跑使用这里配置的默认值。</p>
              </div>
              <span class="text-[10px] text-violet-600 dark:text-violet-300">{{ customParameterConfigs.length }} 个参数</span>
            </div>
            <div
              v-for="config in customParameterConfigs"
              :key="config.name"
              class="grid grid-cols-1 gap-2 rounded-lg border border-violet-100 bg-white/80 p-2.5 dark:border-violet-900/40 dark:bg-gray-900/60 md:grid-cols-[1fr_110px_1fr]"
            >
              <div class="min-w-0">
                <div class="mb-1 flex items-center gap-2">
                  <code class="text-[11px] font-bold text-violet-700 dark:text-violet-300" v-text="'{{' + config.name + '}}'"></code>
                  <span class="text-[10px] text-gray-400">参数配置</span>
                </div>
                <input
                  v-model="config.label"
                  type="text"
                  class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-violet-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200"
                  placeholder="显示名称，例如：部门"
                />
              </div>
              <select
                v-model="config.type"
                class="h-8 rounded-lg border border-gray-200 bg-white px-2 text-xs text-gray-700 outline-none focus:border-violet-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200"
              >
                <option value="text">文本</option>
                <option value="number">数字</option>
                <option value="select">下拉选择</option>
              </select>
              <div>
                <input
                  v-model="config.defaultValue"
                  :type="config.type === 'number' ? 'number' : 'text'"
                  class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-violet-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200"
                  :placeholder="config.type === 'select' ? '默认值' : '默认值（试跑使用）'"
                />
                <input
                  v-if="config.type === 'select'"
                  v-model="config.optionsText"
                  type="text"
                  class="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-violet-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200"
                  placeholder="候选值，用逗号分隔，例如：华东, 华南"
                />
                <label class="mt-1.5 inline-flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400">
                  <input v-model="config.required" type="checkbox" class="rounded border-gray-300 text-violet-600 focus:ring-violet-500" />
                  必填参数
                </label>
              </div>
            </div>
          </div>
        </div>

        <!-- 试跑结果预览区 -->
        <div v-if="testError" class="p-3.5 rounded-xl border border-red-200 bg-red-50/70 text-red-700 text-xs flex items-start gap-2">
          <ExclamationCircleIcon class="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
          <div class="overflow-x-auto select-all font-mono">
            <strong>试跑失败：</strong>{{ testError }}
          </div>
        </div>

        <div v-if="testResult" class="p-4 rounded-xl border border-emerald-200 bg-emerald-50/40 dark:bg-emerald-950/20 space-y-3">
          <div class="flex items-center justify-between text-xs">
            <span class="font-bold text-emerald-800 dark:text-emerald-300 flex items-center gap-1.5">
              <CheckCircleIcon class="w-4 h-4 text-emerald-600" />
              试跑成功！已预览前 {{ testResult.rows?.length || 0 }} 行数据
            </span>
            <span v-if="testResult.execution_time_ms != null" class="text-gray-500 text-[11px] font-mono">
              执行耗时: {{ testResult.execution_time_ms }} ms
            </span>
          </div>

          <!-- 表格预览 -->
          <div class="max-h-48 overflow-x-auto overflow-y-auto rounded-lg border border-emerald-200/80 bg-white dark:bg-gray-900 custom-scrollbar text-xs">
            <table class="w-full text-left border-collapse font-mono text-[11px]">
              <thead class="bg-emerald-100/50 dark:bg-gray-800 sticky top-0">
                <tr>
                  <th
                    v-for="(col, i) in testResult.columns"
                    :key="i"
                    class="px-3 py-1.5 font-bold text-gray-700 dark:text-gray-300 border-b border-emerald-100 dark:border-gray-700 whitespace-nowrap"
                  >
                    {{ typeof col === 'string' ? col : col.name }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
                <tr
                  v-for="(row, rIdx) in testResult.rows"
                  :key="rIdx"
                  class="hover:bg-emerald-50/30 dark:hover:bg-gray-800/50"
                >
                  <td
                    v-for="(val, cIdx) in (Array.isArray(row) ? row : Object.values(row))"
                    :key="cIdx"
                    class="px-3 py-1 text-gray-600 dark:text-gray-400 whitespace-nowrap"
                  >
                    {{ val === null ? 'NULL' : String(val) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Modal Footer -->
      <div class="px-6 py-4 border-t border-gray-100 dark:border-gray-800 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
        <div class="text-xs text-gray-400">
          <span v-if="testPassed" class="text-emerald-600 font-bold">✓ 已通过试跑验证</span>
          <span v-else>提示：建议先点击「试跑测试」确保 SQL 语法与结果符合预期</span>
        </div>

        <div class="flex items-center gap-3">
          <button
            type="button"
            class="px-4 py-2 text-xs font-bold text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-colors cursor-pointer"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            type="button"
            class="px-5 py-2 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-md shadow-blue-500/20 transition-all cursor-pointer disabled:opacity-50 flex items-center gap-1.5"
            :disabled="saving || !form.title.trim() || !form.sqlContent.trim() || !testPassed"
            @click="handleSubmit"
          >
            <ArrowPathIcon v-if="saving" class="w-3.5 h-3.5 animate-spin" />
            <span>{{ saving ? '正在保存...' : activeReport?.id ? '保存报表修改' : '固化保存此报表' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- 动态参数试跑选择器：只影响本次预览，不修改保存后的默认运行参数 -->
  <div
    v-if="visible && showTestParameterModal"
    class="fixed inset-0 z-[270] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
    :class="overlayClass"
    :style="overlayStyle"
    @click.self="closeTestParameterModal"
  >
    <div class="w-full max-w-md overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800">
      <div class="flex items-center justify-between border-b border-gray-100 bg-gray-50/60 px-6 py-4 dark:border-gray-700 dark:bg-gray-800/60">
        <div>
          <h3 class="text-base font-black text-gray-800 dark:text-gray-100">选择试跑参数</h3>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">仅用于本次 SQL 试跑，不会修改报表默认运行参数</p>
        </div>
        <button
          type="button"
          class="rounded-full p-2 text-gray-400 transition-colors hover:bg-gray-200 dark:hover:bg-gray-700"
          @click="closeTestParameterModal"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <div class="space-y-4 p-6">
        <div v-if="hasTestDateParameters">
          <label class="mb-2 block text-xs font-black uppercase tracking-wider text-gray-500 dark:text-gray-400">日期范围</label>
          <select
            v-model="testParameterForm.dateRange"
            class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 focus:border-primary focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100"
          >
            <option value="today">今天</option>
            <option value="yesterday">昨天</option>
            <option value="last_7_days">最近 7 天</option>
            <option value="month_start_to_today">本月截至今天</option>
            <option value="year_start_to_today">今年（年初至今天）</option>
            <option value="custom_range">自定义日期</option>
          </select>
        </div>
        <div v-if="hasTestDateParameters && testParameterForm.dateRange === 'custom_range'" class="grid grid-cols-2 gap-3">
          <label class="text-xs text-gray-500 dark:text-gray-400">
            开始日期
            <input v-model="testParameterForm.startDate" type="date" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100" />
          </label>
          <label class="text-xs text-gray-500 dark:text-gray-400">
            结束日期
            <input v-model="testParameterForm.endDate" type="date" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100" />
          </label>
        </div>

        <div v-if="hasTestMonthParameters">
          <label class="mb-2 block text-xs font-black uppercase tracking-wider text-gray-500 dark:text-gray-400">月份范围</label>
          <select
            v-model="testParameterForm.monthRange"
            class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 focus:border-primary focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100"
          >
            <option value="last_6_completed_months">最近 6 个完整月</option>
            <option value="year_start_to_current_month">本年截至本月</option>
            <option value="custom_month_range">自定义月份</option>
          </select>
        </div>
        <div v-if="hasTestMonthParameters && testParameterForm.monthRange === 'custom_month_range'" class="grid grid-cols-2 gap-3">
          <label class="text-xs text-gray-500 dark:text-gray-400">
            开始月份
            <input v-model="testParameterForm.startMonth" type="month" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100" />
          </label>
          <label class="text-xs text-gray-500 dark:text-gray-400">
            结束月份
            <input v-model="testParameterForm.endMonth" type="month" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100" />
          </label>
        </div>

        <div
          v-for="config in customParameterConfigs"
          :key="config.name"
          class="space-y-1.5"
        >
          <label class="block text-xs font-black uppercase tracking-wider text-gray-500 dark:text-gray-400">
            {{ config.label || config.name }}
            <span v-if="config.required" class="text-red-500">*</span>
          </label>
          <select
            v-if="config.type === 'select'"
            v-model="testParameterForm.customParams[config.name]"
            class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 focus:border-primary focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100"
          >
            <option v-for="option in customParameterOptions(config)" :key="option" :value="option">{{ option }}</option>
          </select>
          <input
            v-else
            v-model="testParameterForm.customParams[config.name]"
            :type="config.type === 'number' ? 'number' : 'text'"
            :placeholder="config.type === 'number' ? '请输入数字' : `请输入${config.label || config.name}`"
            class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 focus:border-primary focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 disabled:bg-gray-100"
          />
        </div>

        <p v-if="testParameterError" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">
          {{ testParameterError }}
        </p>
      </div>

      <div class="flex justify-end gap-3 border-t border-gray-100 bg-gray-50/50 px-6 py-4 dark:border-gray-700 dark:bg-gray-800/50">
        <button
          type="button"
          class="rounded-xl border border-gray-200 px-4 py-2 text-xs font-bold text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:text-gray-400 dark:hover:bg-gray-700"
          @click="closeTestParameterModal"
        >
          取消
        </button>
        <button
          type="button"
          class="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
          :disabled="testing"
          @click="executeTestSql"
        >
          {{ testing ? '正在试跑...' : '确认试跑' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar-embed::-webkit-scrollbar {
  width: 4px;
}

.custom-scrollbar-embed::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.5);
  border-radius: 2px;
}
</style>
