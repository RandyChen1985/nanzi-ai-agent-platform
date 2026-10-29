<script setup lang="ts">
/**
 * 脚本工具列表（「工具注册表」的脚本 tab）。
 *
 * 与 API 工具列表分开成组件：两类工具的筛选维度、列、创建表单都不同，
 * 硬塞进同一张表会让一半的列常年显示「—」。这里自治（自己调接口、自己管弹窗），
 * 父组件 ToolRegistry 只负责 tab 切换。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { scriptToolApi, type ScriptTool, type ScriptToolCapabilities, type ScriptToolPayload, type ScriptToolPreviewResult } from '@/api/tool'
import { parseToolSchema, type SchemaParam } from '@/utils/toolSchema'
import {
  buildParamsFromForm,
  defaultValues,
  enumOf,
  isMultiline,
  itemEnumOf,
  valuesFromObject,
} from '@/utils/scriptToolParams'
import { useToast } from '@/composables/useToast'
import GroupSelect from './GroupSelect.vue'
import ScriptToolWizard from './ScriptToolWizard.vue'
import ScriptToolHelpModal from './ScriptToolHelpModal.vue'
import Modal from '../Modal.vue'
import ConfirmModal from '../ConfirmModal.vue'

const props = defineProps<{
  canSave: boolean
}>()

const { showToast } = useToast()

/** 弹窗打开时页面级错误会被 Modal 盖住，必须同时给一条全局提示 */
function reportError(title: string, error: any) {
  const detail = error?.response?.data?.detail || error?.message || title
  const text = typeof detail === 'string' ? detail : JSON.stringify(detail)
  errorMessage.value = text
  if (wizardVisible.value || previewVisible.value) {
    showToast(text, 'error')
  }
}

const emit = defineEmits<{
  (e: 'count', value: number): void
}>()

const tools = ref<ScriptTool[]>([])
const capabilities = ref<ScriptToolCapabilities | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const searchQuery = ref('')
const sourceFilter = ref<'all' | 'builtin' | 'custom'>('all')
const statusFilter = ref<'all' | 'active' | 'inactive'>('all')
const groupFilter = ref('all')

const wizardVisible = ref(false)
const wizardTool = ref<ScriptTool | null>(null)
const wizardSaving = ref(false)
const helpVisible = ref(false)

const previewVisible = ref(false)
const previewTool = ref<ScriptTool | null>(null)
const previewParams = ref('{}')
const previewRunning = ref(false)
const previewResult = ref<ScriptToolPreviewResult | null>(null)

const UNGROUPED = '__ungrouped__'

// 筛选收进面板：三个下拉常驻会把搜索框挤窄，也占掉列表首屏
const showFilterPanel = ref(false)
const selectedIds = ref<Set<string>>(new Set())
const showBatchGroup = ref(false)
const batchGroupName = ref<string | null>(null)
const batchSaving = ref(false)

const filteredTools = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  return tools.value.filter((tool) => {
    if (sourceFilter.value !== 'all' && tool.source !== sourceFilter.value) return false
    if (statusFilter.value === 'active' && !tool.is_active) return false
    if (statusFilter.value === 'inactive' && tool.is_active) return false
    if (groupFilter.value === UNGROUPED && tool.group_name) return false
    if (groupFilter.value !== 'all' && groupFilter.value !== UNGROUPED && tool.group_name !== groupFilter.value) {
      return false
    }
    if (!keyword) return true
    return [tool.name, tool.display_name, tool.description]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})

/** 分组 → 工具数，供筛选面板与批量分组下拉展示 */
const groupCounts = computed(() => {
  const map = new Map<string, number>()
  for (const tool of tools.value) {
    if (!tool.group_name) continue
    map.set(tool.group_name, (map.get(tool.group_name) || 0) + 1)
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, count]) => ({ name, count }))
})

const activeFilterCount = computed(() => {
  let count = 0
  if (sourceFilter.value !== 'all') count += 1
  if (statusFilter.value !== 'all') count += 1
  if (groupFilter.value !== 'all') count += 1
  return count
})

const selectableTools = computed(() => filteredTools.value.filter((tool) => !isFallback(tool) && tool.id))

const allSelected = computed(
  () =>
    selectableTools.value.length > 0 &&
    selectableTools.value.every((tool) => selectedIds.value.has(String(tool.id))),
)

function toggleAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
    return
  }
  selectedIds.value = new Set(selectableTools.value.map((tool) => String(tool.id)))
}

function toggleOne(tool: ScriptTool) {
  const id = String(tool.id)
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

// 筛选或刷新后清掉已不存在的选中项，避免"批量改了看不见的工具"
watch(filteredTools, () => {
  const visible = new Set(selectableTools.value.map((tool) => String(tool.id)))
  const kept = [...selectedIds.value].filter((id) => visible.has(id))
  if (kept.length !== selectedIds.value.size) selectedIds.value = new Set(kept)
})

function openBatchGroup() {
  batchGroupName.value = null
  showBatchGroup.value = true
}

async function confirmBatchGroup() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  batchSaving.value = true
  try {
    await scriptToolApi.batchSetGroup(ids, batchGroupName.value)
    showBatchGroup.value = false
    selectedIds.value = new Set()
    await load()
  } catch (error: any) {
    reportError('批量设置分组失败', error)
  } finally {
    batchSaving.value = false
  }
}

const hasFilters = computed(
  () =>
    Boolean(searchQuery.value.trim()) ||
    sourceFilter.value !== 'all' ||
    statusFilter.value !== 'all' ||
    groupFilter.value !== 'all',
)

function clearFilters() {
  searchQuery.value = ''
  sourceFilter.value = 'all'
  statusFilter.value = 'all'
  groupFilter.value = 'all'
}

function paramSummary(tool: ScriptTool): string {
  const schema = (tool.parameter_schema || {}) as Record<string, any>
  const properties = (schema.properties || schema) as Record<string, any>
  const required = Array.isArray(schema.required) ? (schema.required as string[]) : []
  const names = Object.keys(properties || {}).filter(
    (key) =>
      !key.startsWith('x-') &&
      !['type', 'required', '$schema', 'description', 'properties', 'definitions', 'additionalProperties'].includes(key),
  )
  if (!names.length) return '无参数'
  return names
    .map((name) => (required.includes(name) ? `${name}（必填）` : name))
    .join('、')
}

/** 迁移漏跑时后端返回代码库兜底行：能看，但没有库行可编辑。
 *
 * 不能按 id 前缀猜——库里内置行的 id 本来就叫 builtin-script-*，
 * 那样会把「正常的库行」也判成兜底，页面永远提示「迁移未执行」并锁死新建。 */
function isFallback(tool: ScriptTool): boolean {
  return tool.is_fallback === true
}

const hasFallbackRows = computed(() => tools.value.some(isFallback))
// 只有「一条库行都没有」才需要锁新建；部分内置 seed 缺失时，其他库行仍可编辑
const hasFallback = computed(() => tools.value.length > 0 && tools.value.every(isFallback))

async function load() {
  loading.value = true
  errorMessage.value = ''
  // 两个请求分开处理：capabilities 挂了不该让已经拿到的列表也一起丢掉
  const [listRes, capRes] = await Promise.allSettled([
    scriptToolApi.list(),
    scriptToolApi.capabilities(),
  ])

  if (listRes.status === 'fulfilled') {
    const data = listRes.value.data
    tools.value = Array.isArray(data) ? data : (data as any)?.data || []
    emit('count', tools.value.length)
  } else {
    reportError('加载脚本工具失败', listRes.reason)
  }
  if (capRes.status === 'fulfilled') {
    capabilities.value = capRes.value.data
  } else {
    reportError('加载脚本工具运行环境失败', capRes.reason)
  }
  loading.value = false
}

function openCreate() {
  wizardTool.value = null
  wizardVisible.value = true
}

function openEdit(tool: ScriptTool) {
  wizardTool.value = tool
  wizardVisible.value = true
}

function openCopy(tool: ScriptTool) {
  wizardTool.value = { ...tool, id: '', name: `${tool.name}_copy`, source: 'custom', is_active: false }
  wizardVisible.value = true
}

async function handleSubmit(payload: ScriptToolPayload) {
  wizardSaving.value = true
  try {
    const editingId = wizardTool.value?.id
    if (editingId) {
      await scriptToolApi.update(editingId, payload)
    } else {
      await scriptToolApi.create(payload)
    }
    wizardVisible.value = false
    await load()
  } catch (error: any) {
    reportError('保存失败', error)
  } finally {
    wizardSaving.value = false
  }
}

async function toggleActive(tool: ScriptTool) {
  try {
    await scriptToolApi.update(tool.id, { is_active: !tool.is_active })
    await load()
  } catch (error: any) {
    reportError('切换状态失败', error)
  }
}

const pendingDelete = ref<ScriptTool | null>(null)
const pendingReset = ref<ScriptTool | null>(null)

async function confirmReset() {
  const tool = pendingReset.value
  pendingReset.value = null
  if (!tool) return
  try {
    await scriptToolApi.resetToBuiltin(tool.id)
    await load()
  } catch (error: any) {
    reportError('恢复失败', error)
  }
}

async function confirmDelete() {
  const tool = pendingDelete.value
  pendingDelete.value = null
  if (!tool) return
  try {
    await scriptToolApi.delete(tool.id)
    await load()
  } catch (error: any) {
    reportError('删除失败', error)
  }
}

/* ---------------------------- 试运行：参数表单 ----------------------------
 * 让业务方对着一个空 JSON 框猜该传什么，等于没做这个功能。字段、默认值、
 * 类型转换全部来自 parameter_schema，逻辑放在 utils/scriptToolParams.ts
 * （纯函数，有 Node 单测）；这里只负责把它们接到界面上。
 */
type PreviewField = SchemaParam

const previewSchema = computed(() => {
  const schema = previewTool.value?.parameter_schema
  if (!schema) return null
  try {
    return parseToolSchema(JSON.stringify(schema))
  } catch {
    return null
  }
})

const previewFields = computed<PreviewField[]>(() => previewSchema.value?.params ?? [])
const previewValues = ref<Record<string, any>>({})
const previewMode = ref<'form' | 'json'>('form')
const previewFormError = ref('')

function previewFieldsFor(tool: ScriptTool): PreviewField[] {
  try {
    return parseToolSchema(JSON.stringify(tool.parameter_schema || {}))?.params ?? []
  } catch {
    return []
  }
}

function openPreview(tool: ScriptTool) {
  previewTool.value = tool
  previewResult.value = null
  previewFormError.value = ''
  previewMode.value = 'form'
  previewValues.value = defaultValues(previewFieldsFor(tool))
  previewParams.value = '{}'
  previewVisible.value = true
}

function switchPreviewMode(next: 'form' | 'json') {
  if (next === previewMode.value) return
  if (next === 'json') {
    previewParams.value = JSON.stringify(
      buildParamsFromForm(previewFields.value, previewValues.value).params,
      null,
      2,
    )
    previewFormError.value = ''
  } else {
    try {
      const parsed = JSON.parse(previewParams.value || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('not an object')
      }
      previewValues.value = valuesFromObject(previewFields.value, parsed)
      previewFormError.value = ''
    } catch {
      previewFormError.value = 'JSON 模式的内容不是合法对象，先修正再切回表单'
      return
    }
  }
  previewMode.value = next
}

async function runPreview() {
  if (!previewTool.value) return
  let params: Record<string, any>
  if (previewMode.value === 'form') {
    const built = buildParamsFromForm(previewFields.value, previewValues.value)
    if (built.error) {
      previewFormError.value = built.error
      return
    }
    previewFormError.value = ''
    params = built.params
  } else {
    try {
      const parsed = JSON.parse(previewParams.value || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('not an object')
      }
      params = parsed
    } catch {
      previewFormError.value = 'JSON 模式的内容必须是合法对象'
      return
    }
    previewFormError.value = ''
  }
  previewRunning.value = true
  try {
    const res = await scriptToolApi.preview({ tool_id: previewTool.value.id, params })
    previewResult.value = res.data
  } catch (error: any) {
    previewResult.value = {
      ok: false,
      stdout: '',
      stderr: error?.response?.data?.detail || error?.message || '试运行失败',
      duration_ms: 0,
      timed_out: false,
      message: '',
    } as ScriptToolPreviewResult
  } finally {
    previewRunning.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <!-- 运行环境提示：local 策略下不能谎称在沙箱里执行 -->
    <div
      v-if="capabilities"
      class="flex items-start gap-2 border-b px-4 py-2.5 text-xs"
      :class="capabilities.script_tool_supported ? 'border-blue-100 bg-blue-50/70 text-blue-900' : 'border-amber-200 bg-amber-50 text-amber-900'"
    >
      <span
        class="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
        :class="capabilities.script_tool_supported ? 'bg-blue-600' : 'bg-amber-600'"
      >{{ capabilities.script_tool_supported ? 'i' : '!' }}</span>
      <span class="leading-relaxed">
        <strong>{{ capabilities.sandbox_label }}</strong> · {{ capabilities.notice }}
      </span>
    </div>

    <div v-if="hasFallbackRows" class="border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs leading-relaxed text-amber-800">
      <template v-if="hasFallback">脚本工具表尚未初始化（迁移未执行），当前仅展示内置定义；新建、编辑、启停、试运行都需要先执行对应的数据库迁移。</template>
      <template v-else>部分内置脚本缺少库行（迁移未完整执行），这些行会禁用编辑/启停/试运行；其他已有库行的工具不受影响。</template>
    </div>

    <!-- 筛选收进面板：来源/状态/分组三个下拉常驻会把搜索框挤窄，也占掉列表首屏 -->
    <div class="flex flex-wrap items-center gap-2 px-4 py-3">
      <input
        v-model="searchQuery"
        type="search"
        placeholder="搜索名称、描述或用途..."
        class="min-w-[12rem] flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
      />

      <div class="relative shrink-0" @keydown.esc="showFilterPanel = false">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg border bg-white px-3 py-2 text-sm font-medium shadow-sm transition-colors"
          :class="activeFilterCount ? 'border-primary/40 text-gray-800' : 'border-gray-300 text-gray-700 hover:bg-gray-50'"
          :aria-expanded="showFilterPanel"
          aria-haspopup="true"
          @click="showFilterPanel = !showFilterPanel"
        >
          <svg class="h-4 w-4" :class="activeFilterCount ? 'text-primary' : 'text-gray-400'" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5h18l-7 8v6l-4-2v-4z" />
          </svg>
          筛选
          <span
            v-if="activeFilterCount"
            class="inline-flex min-w-[1.125rem] items-center justify-center rounded-full bg-primary/10 px-1 text-[10px] font-bold text-primary"
          >{{ activeFilterCount }}</span>
          <svg class="h-4 w-4 text-gray-400 transition-transform" :class="{ 'rotate-180': showFilterPanel }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 9l6 6 6-6" />
          </svg>
        </button>

        <div
          v-if="showFilterPanel"
          class="absolute right-0 z-30 mt-1.5 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white p-3 shadow-lg"
          role="group"
          aria-label="筛选条件"
        >
          <div>
            <p class="mb-1.5 text-xs font-semibold text-gray-500">来源</p>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="opt in [{ value: 'all', label: '全部' }, { value: 'builtin', label: '内置' }, { value: 'custom', label: '自定义' }]"
                :key="`src-${opt.value}`"
                type="button"
                class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                :class="sourceFilter === opt.value
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                :aria-pressed="sourceFilter === opt.value"
                @click="sourceFilter = opt.value as any"
              >{{ opt.label }}</button>
            </div>
          </div>

          <div class="my-3 border-t border-gray-100"></div>

          <div>
            <p class="mb-1.5 text-xs font-semibold text-gray-500">状态</p>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="opt in [{ value: 'all', label: '全部' }, { value: 'active', label: '启用' }, { value: 'inactive', label: '停用' }]"
                :key="`st-${opt.value}`"
                type="button"
                class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                :class="statusFilter === opt.value
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                :aria-pressed="statusFilter === opt.value"
                @click="statusFilter = opt.value as any"
              >{{ opt.label }}</button>
            </div>
          </div>

          <template v-if="groupCounts.length">
            <div class="my-3 border-t border-gray-100"></div>
            <div>
              <p class="mb-1.5 text-xs font-semibold text-gray-500">业务分组</p>
              <select
                v-model="groupFilter"
                class="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option value="all">全部分组</option>
                <option :value="UNGROUPED">未分组</option>
                <option v-for="group in groupCounts" :key="group.name" :value="group.name">
                  {{ group.name }}（{{ group.count }}）
                </option>
              </select>
            </div>
          </template>

          <div class="mt-3 flex items-center justify-between border-t border-gray-100 pt-2.5">
            <span class="text-[11px] text-gray-400">
              {{ activeFilterCount ? `已选 ${activeFilterCount} 项` : '未设置筛选' }}
            </span>
            <button
              type="button"
              class="text-xs font-medium text-primary transition-colors hover:text-primary-dark disabled:cursor-not-allowed disabled:text-gray-300"
              :disabled="!activeFilterCount"
              @click="sourceFilter = 'all'; statusFilter = 'all'; groupFilter = 'all'"
            >清除全部</button>
          </div>
        </div>
      </div>

      <button
        type="button"
        class="flex h-[34px] w-[30px] shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-sm font-bold text-gray-500 shadow-sm hover:border-primary hover:bg-blue-50 hover:text-primary"
        title="查看脚本工具编写指南"
        @click="helpVisible = true"
      >?</button>
      <button
        type="button"
        class="shrink-0 px-2.5 py-2 text-sm text-gray-500 hover:text-primary"
        :class="{ invisible: !hasFilters }"
        @click="clearFilters"
      >清空</button>
      <button
        v-if="props.canSave"
        type="button"
        class="shrink-0 rounded-lg bg-primary px-3 py-2 text-sm text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="hasFallback || !capabilities || !capabilities.script_tool_supported"
        :title="hasFallback ? '迁移未执行，暂不可新建' : (!capabilities ? '运行环境信息不可用，暂不能新建' : (capabilities.script_tool_supported ? '新建脚本工具' : '当前沙箱策略暂不支持脚本工具'))"
        @click="openCreate"
      >+ 添加脚本工具</button>
    </div>

    <!-- 选中后浮出的批量操作条：只在勾选时出现，避免常驻按钮误点 -->
    <div
      v-if="selectedIds.size"
      class="mx-4 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2"
    >
      <span class="text-xs font-medium text-gray-700">已选 {{ selectedIds.size }} 个</span>
      <button
        v-if="props.canSave"
        type="button"
        class="rounded-md border border-primary/40 bg-white px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
        @click="openBatchGroup"
      >批量设置分组</button>
      <button
        type="button"
        class="text-xs text-gray-500 transition-colors hover:text-gray-700"
        @click="selectedIds = new Set()"
      >取消选择</button>
    </div>

    <div v-if="errorMessage" class="mx-4 mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
      {{ errorMessage }}
    </div>

    <div v-if="loading" class="px-4 py-10 text-center text-sm text-gray-400">加载中…</div>
    <div v-else-if="!filteredTools.length" class="px-4 py-12 text-center text-sm text-gray-400">
      {{ hasFilters ? '没有匹配的脚本工具，试试清空筛选。' : '还没有脚本工具，点右上角「+ 添加脚本工具」新建一个，或先看「?」指南。' }}
    </div>

    <table v-else class="w-full">
      <thead>
        <tr class="border-y border-gray-200 bg-gray-50">
          <th class="w-[40px] px-3 py-2.5">
            <input
              type="checkbox"
              class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
              :checked="allSelected"
              :disabled="!selectableTools.length"
              :aria-label="allSelected ? '取消全选' : '全选'"
              @change="toggleAll"
            />
          </th>
          <th class="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400">名称</th>
          <th class="w-[90px] px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400">语言</th>
          <th class="w-[90px] px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400">来源</th>
          <th class="w-[120px] px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-400">业务分组</th>
          <th class="w-[110px] px-3 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-gray-400">状态</th>
          <th class="w-[130px] px-3 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-gray-400">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="tool in filteredTools"
          :key="tool.id || tool.name"
          class="border-b border-gray-100 align-middle hover:bg-blue-50/30"
          :class="{ 'bg-primary/5': tool.id && selectedIds.has(String(tool.id)) }"
        >
          <td class="px-3 py-3.5">
            <input
              v-if="!isFallback(tool) && tool.id"
              type="checkbox"
              class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
              :checked="selectedIds.has(String(tool.id))"
              :aria-label="`选择 ${tool.name}`"
              @change="toggleOne(tool)"
            />
          </td>
          <td class="px-3 py-3.5">
            <div class="flex items-center gap-2">
              <span class="font-mono text-[13px] font-semibold text-gray-900">{{ tool.name }}</span>
              <span v-if="tool.display_name" class="text-xs text-gray-400">{{ tool.display_name }}</span>
            </div>
            <div class="mt-1 max-w-[640px] text-xs leading-relaxed text-gray-500">{{ tool.description }}</div>
            <div class="mt-1 font-mono text-[11px] text-gray-400">参数：{{ paramSummary(tool) }}</div>
            <div v-if="tool.requirements?.length" class="mt-0.5 font-mono text-[11px] text-amber-600">
              依赖：{{ tool.requirements.join('、') }}
            </div>
          </td>
          <td class="px-3 py-3.5">
            <span class="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-emerald-700">
              {{ (tool.language || 'python').toUpperCase() }}
            </span>
          </td>
          <td class="px-3 py-3.5">
            <span
              class="rounded px-1.5 py-0.5 text-[10px] font-bold"
              :class="tool.source === 'builtin' ? 'bg-violet-50 text-violet-700' : 'bg-orange-50 text-orange-700'"
            >{{ tool.source === 'builtin' ? '内置' : '自定义' }}</span>
            <span
              v-if="tool.is_overridden"
              class="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-700"
              title="代码/参数/依赖/超时已在本平台改过：代码库版本不再生效"
            >已覆盖</span>
          </td>
          <td class="px-3 py-3.5 text-[13px] text-gray-600">{{ tool.group_name || '—' }}</td>
          <td class="px-3 py-3.5">
            <div class="flex items-center justify-end gap-2">
              <span
                class="rounded px-1.5 py-0.5 text-[11px]"
                :class="tool.is_active ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400'"
              >{{ tool.is_active ? '启用' : '停用' }}</span>
              <button
                type="button"
                class="relative inline-flex h-5 w-[38px] shrink-0 items-center rounded-full transition-colors"
                :class="tool.is_active ? 'bg-primary' : 'bg-gray-300'"
                :disabled="!props.canSave || isFallback(tool)"
                :title="isFallback(tool) ? '迁移未执行，暂不可操作' : (tool.is_active ? '停用' : '启用')"
                @click="toggleActive(tool)"
              >
                <span
                  class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform"
                  :class="tool.is_active ? 'translate-x-[20px]' : 'translate-x-[2px]'"
                />
              </button>
            </div>
          </td>
          <td class="px-3 py-3.5">
            <div class="flex items-center justify-end gap-2.5 text-gray-400">
              <button v-if="canSave && !isFallback(tool)" type="button" class="hover:text-primary" title="试运行" @click="openPreview(tool)">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 5v14l11-7z" /></svg>
              </button>
              <button
                v-if="canSave && !isFallback(tool)"
                type="button"
                class="hover:text-primary"
                :title="tool.source === 'builtin' ? '编辑内置脚本（保存后标记为「已覆盖」）' : '编辑'"
                @click="openEdit(tool)"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
              </button>
              <button
                v-if="tool.is_overridden && canSave && !isFallback(tool)"
                type="button"
                class="hover:text-amber-600"
                title="恢复内置默认（放弃本平台的改动）"
                @click="pendingReset = tool"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /></svg>
              </button>
              <button v-if="canSave && !isFallback(tool)" type="button" class="hover:text-primary" title="复制为新工具" @click="openCopy(tool)">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
              </button>
              <button
                type="button"
                :class="tool.source === 'builtin' || isFallback(tool) ? 'cursor-not-allowed opacity-40' : 'hover:text-red-500'"
                :title="isFallback(tool) ? '迁移未执行，暂不可操作' : (tool.source === 'builtin' ? '内置脚本不能删除，可停用' : '删除')"
                :disabled="tool.source === 'builtin' || isFallback(tool)"
                @click="tool.source === 'builtin' || isFallback(tool) ? undefined : (pendingDelete = tool)"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></svg>
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <ScriptToolWizard
      :show="wizardVisible"
      :group-options="groupCounts"
      :tool="wizardTool"
      :sandbox-notice="capabilities?.notice"
      :saving="wizardSaving"
      @close="wizardVisible = false"
      @submit="handleSubmit"
    />

    <ScriptToolHelpModal
      :show="helpVisible"
      :sandbox-notice="capabilities?.notice"
      @close="helpVisible = false"
    />

    <!-- 试运行 -->
    <Modal
      title="批量设置业务分组"
      size="max-w-md"
      :show="showBatchGroup"
      @close="showBatchGroup = false"
    >
      <p class="text-sm text-gray-500">
        将对已选中的 {{ selectedIds.size }} 个脚本工具设置业务分组；留空表示清除分组。
      </p>
      <div class="mt-4">
        <label for="script-batch-group" class="block text-sm font-medium text-gray-700">业务分组</label>
        <GroupSelect
          id="script-batch-group"
          v-model="batchGroupName"
          class="mt-1"
          :options="groupCounts"
          placeholder="选择已有分组，或直接输入新分组（留空 = 未分组）"
        />
        <p class="mt-1 text-xs text-gray-500">与 API 工具共用同一套分组，智能体配置时会按组归类</p>
      </div>
      <div class="mt-5 flex justify-end gap-3">
        <button
          type="button"
          class="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          @click="showBatchGroup = false"
        >取消</button>
        <button
          type="button"
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="batchSaving"
          @click="confirmBatchGroup"
        >{{ batchSaving ? '保存中…' : '确定' }}</button>
      </div>
    </Modal>

    <Modal
      title="试运行"
      size="max-w-3xl"
      :show="previewVisible"
      @close="previewVisible = false"
    >
      <div class="-mt-2">
        <div class="mb-3">
          <div class="text-sm font-semibold text-gray-900">{{ previewTool?.name }}</div>
          <p class="mt-0.5 text-xs text-gray-400">
            在平台后端工作区的预览目录中执行，不写库{{ capabilities?.script_tool_supported ? '；真实调用在会话沙箱内执行' : '' }}
          </p>
        </div>
        <div class="mb-2 flex items-center justify-between">
          <span class="text-xs font-medium text-gray-600">测试参数</span>
          <div class="flex rounded-lg bg-gray-100 p-0.5 text-xs" role="group" aria-label="参数填写方式">
            <button
              type="button"
              class="rounded-md px-2 py-1 font-medium transition-all"
              :class="previewMode === 'form' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'"
              @click="switchPreviewMode('form')"
            >表单</button>
            <button
              type="button"
              class="rounded-md px-2 py-1 font-medium transition-all"
              :class="previewMode === 'json' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'"
              @click="switchPreviewMode('json')"
            >JSON</button>
          </div>
        </div>

        <!-- 表单模式：按参数定义生成控件，别让人对着空 JSON 猜 -->
        <div v-if="previewMode === 'form'">
          <p v-if="previewFields.length === 0" class="rounded-lg border border-dashed border-gray-200 px-3 py-6 text-center text-xs italic text-gray-400">
            这个工具不需要输入参数，直接点「运行」即可。
          </p>
          <div v-else class="space-y-3">
            <div v-for="field in previewFields" :key="field.name" class="space-y-1">
              <label class="flex flex-wrap items-center gap-1.5 text-xs">
                <span class="font-mono font-medium text-gray-800">{{ field.name }}</span>
                <span v-if="field.required" class="text-red-500" title="必填">*</span>
                <span class="rounded bg-gray-100 px-1 py-0.5 text-[10px] font-medium text-gray-500">{{ field.type }}</span>
                <span v-if="field.required" class="text-[10px] text-gray-400">必填</span>
              </label>
              <p v-if="field.description" class="text-[11px] leading-relaxed text-gray-500">{{ field.description }}</p>

              <label v-if="field.type === 'boolean'" class="flex items-center gap-2 text-xs text-gray-600">
                <input
                  v-model="previewValues[field.name]"
                  type="checkbox"
                  class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary/30"
                />
                <span>{{ previewValues[field.name] ? '是（true）' : '否（false）' }}</span>
              </label>

              <div v-else-if="field.type === 'array' && itemEnumOf(field)" class="flex flex-wrap gap-2">
                <label
                  v-for="option in itemEnumOf(field) || []"
                  :key="option"
                  class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
                  :class="Array.isArray(previewValues[field.name]) && previewValues[field.name].includes(option)
                    ? 'border-primary bg-blue-50 text-primary'
                    : 'border-gray-300 text-gray-600'"
                >
                  <input
                    type="checkbox"
                    class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary/30"
                    :value="option"
                    v-model="previewValues[field.name]"
                  />
                  {{ option }}
                </label>
                <span v-if="!Array.isArray(previewValues[field.name]) || previewValues[field.name].length === 0" class="text-[11px] text-gray-400">
                  不选表示使用工具的默认行为
                </span>
              </div>

              <select
                v-else-if="enumOf(field)"
                v-model="previewValues[field.name]"
                class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option v-for="option in enumOf(field) || []" :key="option" :value="option">{{ option }}</option>
              </select>

              <textarea
                v-else-if="isMultiline(field)"
                v-model="previewValues[field.name]"
                :rows="field.type === 'array' || field.type === 'object' ? 3 : 4"
                spellcheck="false"
                :placeholder="field.type === 'array' ? '一行一个，或直接写 JSON 数组' : (field.type === 'object' ? '{ }' : '')"
                class="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              ></textarea>

              <input
                v-else-if="field.type === 'integer' || field.type === 'number'"
                v-model="previewValues[field.name]"
                type="number"
                :step="field.type === 'integer' ? 1 : 'any'"
                class="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              />

              <input
                v-else
                v-model="previewValues[field.name]"
                class="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                :placeholder="field.description || field.name"
              />
            </div>
          </div>
        </div>

        <!-- JSON 模式：复杂结构或想整段粘贴时用，与表单值互相同步 -->
        <div v-else>
          <textarea
            v-model="previewParams"
            rows="8"
            spellcheck="false"
            class="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          ></textarea>
          <p class="mt-1 text-[11px] text-gray-400">切回「表单」会把这里的内容回填到各参数输入框。</p>
        </div>

        <p v-if="previewFormError" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{{ previewFormError }}</p>
        <div v-if="previewResult" class="mt-4 space-y-3">
          <div class="flex flex-wrap items-center gap-3 text-xs">
            <span class="rounded px-2 py-0.5 font-medium" :class="previewResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'">
              {{ previewResult.ok ? '成功' : previewResult.timed_out ? '超时' : '失败' }}
            </span>
            <span class="text-gray-500">退出码：{{ previewResult.exit_code ?? '—' }}</span>
            <span class="text-gray-500">耗时：{{ previewResult.duration_ms }} ms</span>
            <!-- 试运行不进沙箱：显示 sandbox_policy 会和上面「在预览目录执行」自相矛盾 -->
            <span class="text-gray-400">运行位置：平台后端预览目录</span>
          </div>
          <div v-if="previewResult.message" class="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{{ previewResult.message }}</div>
          <div>
            <div class="mb-1 text-xs font-medium text-gray-600">stdout</div>
            <pre class="max-h-56 overflow-auto rounded-lg bg-gray-900 p-3 text-[11px] leading-relaxed text-gray-100">{{ previewResult.stdout || '（空）' }}</pre>
          </div>
          <div v-if="previewResult.stderr">
            <div class="mb-1 text-xs font-medium text-gray-600">stderr</div>
            <pre class="max-h-40 overflow-auto rounded-lg bg-red-950 p-3 text-[11px] leading-relaxed text-red-100">{{ previewResult.stderr }}</pre>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button type="button" class="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-600" @click="previewVisible = false">关闭</button>
          <button
            type="button"
            class="rounded-lg bg-primary px-3 py-2 text-sm text-white disabled:opacity-50"
            :disabled="previewRunning"
            @click="runPreview"
          >{{ previewRunning ? '执行中…' : '运行' }}</button>
        </div>
      </template>
    </Modal>

    <ConfirmModal
      v-if="pendingReset"
      title="恢复内置默认版本"
      :message="`「${pendingReset.name}」在本平台的改动会被丢弃，代码与参数回到平台内置版本。之后平台升级会继续更新它。`"
      confirm-text="恢复默认"
      @confirm="confirmReset"
      @cancel="pendingReset = null"
    />

    <ConfirmModal
      v-if="pendingDelete"
      title="确认删除脚本工具"
      :message="`删除「${pendingDelete.name}」后无法恢复；智能体里已引用它的配置会失效。`"
      confirm-text="确认删除"
      @confirm="confirmDelete"
      @cancel="pendingDelete = null"
    />
  </div>
</template>
