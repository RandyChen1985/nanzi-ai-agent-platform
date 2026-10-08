<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
    PARAM_TYPE_OPTIONS,
    parseToolSchema,
    serializeToolSchema,
    type ParsedToolSchema,
    type SchemaParam,
} from '../../utils/toolSchema'
import { toolApi, type SysApiTool, type SysApiToolCreate } from '../../api/tool'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import ConfirmModal from '../ConfirmModal.vue'
import { 
  PencilSquareIcon,
  TrashIcon,
  DocumentDuplicateIcon
} from '@heroicons/vue/24/outline'

const { showToast } = useToast()
const { hasPermission } = useUser()
const canSave = hasPermission('element:system:config_save')

const tools = ref<SysApiTool[]>([])
const loading = ref(false)
const toolSearchQuery = ref('')
const toolMethodFilter = ref('all')
const toolStatusFilter = ref('all')
const showModal = ref(false)
const isEditing = ref(false)
const showDeleteConfirm = ref(false)
const deletingTool = ref<SysApiTool | null>(null)
const groupFilter = ref('all')
const selectedToolIds = ref<Set<string>>(new Set())
const showBatchGroupModal = ref(false)
const batchGroupName = ref('')
// 筛选下拉里代表「没有业务分组」的哨兵值
const UNGROUPED_FILTER = '__ungrouped__'
// 列表视图（扁平行）/ 分组视图（按业务分组折叠）
const viewMode = ref<'list' | 'group'>('list')
const collapsedRegistryGroups = ref<Set<string>>(new Set())

const toolForm = ref<Partial<SysApiToolCreate> & { id?: string; parameter_schema_str: string; headers_str: string }>({
  name: '',
  description: '',
  method: 'GET',
  url_template: '',
  headers_str: '{}',
  parameter_schema_str: '{}',
  is_active: true,
  group_name: ''
})

const filteredTools = computed(() => {
    const keyword = toolSearchQuery.value.trim().toLowerCase()
    return tools.value.filter((tool) => {
        const matchesKeyword = !keyword || [tool.name, tool.description, tool.url_template]
            .some((value) => String(value || '').toLowerCase().includes(keyword))
        const matchesMethod = toolMethodFilter.value === 'all'
            || String(tool.method || '').toUpperCase() === toolMethodFilter.value
        const matchesStatus = toolStatusFilter.value === 'all'
            || (toolStatusFilter.value === 'active' && tool.is_active)
            || (toolStatusFilter.value === 'inactive' && !tool.is_active)
        const groupName = String(tool.group_name || '').trim()
        const matchesGroup = groupFilter.value === 'all'
            || (groupFilter.value === UNGROUPED_FILTER && !groupName)
            || groupName === groupFilter.value
        return matchesKeyword && matchesMethod && matchesStatus && matchesGroup
    })
})

// 分组统计：下拉里带上每组规模，并让「还有多少没归类」一眼可见
const groupFilterOptions = computed(() => {
    const counts = new Map<string, number>()
    let ungrouped = 0
    for (const tool of tools.value) {
        const name = String(tool.group_name || '').trim()
        if (!name) {
            ungrouped += 1
            continue
        }
        counts.set(name, (counts.get(name) || 0) + 1)
    }
    return {
        all: tools.value.length,
        ungrouped,
        groups: Array.from(counts.entries()).sort(
            (a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-Hans-CN')
        ),
    }
})

const ungroupedToolsCount = computed(() => groupFilterOptions.value.ungrouped)

// 已有业务分组名：表单用 datalist 复用，避免同一业务域被写成「履约罚款」「履约罚金」两个组
const existingGroupNames = computed(() => groupFilterOptions.value.groups.map(([name]) => name))

const filterByGroup = (name: string) => {
    groupFilter.value = name
}

type RegistryDisplayGroup = {
    key: string
    label: string
    tools: SysApiTool[]
}

// 两种视图共用同一份行标记：列表视图是「一个匿名分组」，分组视图按 group_name 聚合
// （未分组殿后），这样行结构只写一遍，不会出现两套渲染逐渐漂移。
const displayGroups = computed<RegistryDisplayGroup[]>(() => {
    if (viewMode.value === 'list') {
        return [{ key: '__all__', label: '', tools: filteredTools.value }]
    }
    const buckets = new Map<string, SysApiTool[]>()
    const ungrouped: SysApiTool[] = []
    for (const tool of filteredTools.value) {
        const name = String(tool.group_name || '').trim()
        if (!name) {
            ungrouped.push(tool)
            continue
        }
        const bucket = buckets.get(name)
        if (bucket) bucket.push(tool)
        else buckets.set(name, [tool])
    }
    const groups: RegistryDisplayGroup[] = Array.from(buckets.entries())
        .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh-Hans-CN'))
        .map(([name, list]) => ({ key: name, label: name, tools: list }))
    if (ungrouped.length > 0) {
        groups.push({ key: UNGROUPED_FILTER, label: '未分组', tools: ungrouped })
    }
    return groups
})

const isGroupCollapsed = (key: string) => {
    // 搜索时一律展开：命中项藏在折叠组里等于没搜到
    if (toolSearchQuery.value.trim() || key === '__all__') return false
    return collapsedRegistryGroups.value.has(key)
}

const toggleRegistryGroupCollapse = (key: string) => {
    const next = new Set(collapsedRegistryGroups.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    collapsedRegistryGroups.value = next
}

const getGroupSelectedCount = (groupTools: SysApiTool[]) =>
    groupTools.filter((tool) => selectedToolIds.value.has(tool.id)).length

const isGroupAllSelected = (group: RegistryDisplayGroup) =>
    group.tools.length > 0 && group.tools.every((tool) => selectedToolIds.value.has(tool.id))

const selectGroupTools = (group: RegistryDisplayGroup) => {
    const next = new Set(selectedToolIds.value)
    if (isGroupAllSelected(group)) {
        group.tools.forEach((tool) => next.delete(tool.id))
    } else {
        group.tools.forEach((tool) => next.add(tool.id))
    }
    selectedToolIds.value = next
}

const allFilteredSelected = computed(() => (
    filteredTools.value.length > 0
    && filteredTools.value.every((tool) => selectedToolIds.value.has(tool.id))
))

const toggleToolSelection = (id: string) => {
    const next = new Set(selectedToolIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedToolIds.value = next
}

// 存量工具靠这两步归类：先「选中当前筛选结果」，再「批量设置分组」
const toggleSelectAllFiltered = () => {
    const next = new Set(selectedToolIds.value)
    if (allFilteredSelected.value) {
        filteredTools.value.forEach((tool) => next.delete(tool.id))
    } else {
        filteredTools.value.forEach((tool) => next.add(tool.id))
    }
    selectedToolIds.value = next
}

const openBatchGroupModal = () => {
    batchGroupName.value = ''
    showBatchGroupModal.value = true
}

const confirmBatchGroup = async () => {
    const ids = Array.from(selectedToolIds.value)
    if (ids.length === 0) return
    try {
        const res = await toolApi.batchSetGroup(ids, batchGroupName.value.trim() || null)
        showToast(`已更新 ${res.data.updated} 个工具的业务分组`, 'success')
        showBatchGroupModal.value = false
        selectedToolIds.value = new Set()
        fetchTools()
    } catch (e: any) {
        showToast('批量设置分组失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
}

const toolMethodOptions = computed(() => Array.from(new Set([
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS',
    ...tools.value.map((tool) => String(tool.method || '').toUpperCase()).filter(Boolean),
])))

const hasToolFilters = computed(() => Boolean(
    toolSearchQuery.value.trim()
    || toolMethodFilter.value !== 'all'
    || toolStatusFilter.value !== 'all'
    || groupFilter.value !== 'all'
))

const clearToolFilters = () => {
    toolSearchQuery.value = ''
    toolMethodFilter.value = 'all'
    toolStatusFilter.value = 'all'
    groupFilter.value = 'all'
}

const fetchTools = async () => {
    loading.value = true
    try {
        const res = await toolApi.list()
        tools.value = res.data
    } catch (e: any) {
        showToast('获取工具列表失败', 'error')
    } finally {
        loading.value = false
    }
}

const openModal = (tool?: SysApiTool, isClone = false) => {
    if (tool) {
        isEditing.value = !isClone
        // If clone, reset ID and append copy to name
        const initialForm = { 
            ...tool, 
            id: isClone ? undefined : tool.id,
            name: isClone ? `${tool.name}_copy` : tool.name,
            headers_str: JSON.stringify(tool.headers || {}, null, 2),
            parameter_schema_str: JSON.stringify(tool.parameter_schema || {}, null, 2)
        }
        toolForm.value = initialForm
    } else {
        isEditing.value = false
        toolForm.value = {
            name: '',
            description: '',
            method: 'GET',
            url_template: '',
            headers_str: '{}',
            parameter_schema_str: '{}',
            is_active: true,
            group_name: ''
        }
    }
    // 打开时就按形态预解析一次，切到可视化即是所见
    schemaEditorMode.value = 'json'
    loadVisualFromJson()
    showModal.value = true
}

// ---------------- 参数定义：JSON / 可视化 双模式 ----------------
// 解析与序列化放在 utils/toolSchema.ts（纯函数，可用 Node 直接跑往返测试）。
// 这里只维护 UI 状态：模式、行数据、以及「非法 JSON 不切换」等交互约定。
type VisualParam = SchemaParam & { key: string }

const schemaEditorMode = ref<'json' | 'visual'>('json')
const visualParams = ref<VisualParam[]>([])
const parsedSchema = ref<ParsedToolSchema>({ shape: 'flat', hadRequiredList: false, rest: {}, params: [] })
let visualParamSeed = 0

const nextVisualParamKey = () => {
    visualParamSeed += 1
    return `param-${visualParamSeed}`
}

// 嵌套参数（带 items / properties 子结构）在可视化里只能改类型、描述与必填，
// 子结构本身请回 JSON 模式编辑——这里只做提示。
const visualNestedCount = computed(() => visualParams.value.filter(
    (param) => param.extra.items || param.extra.properties
).length)

const loadVisualFromJson = (): boolean => {
    const parsed = parseToolSchema(toolForm.value.parameter_schema_str)
    if (!parsed) {
        showToast('参数定义不是合法 JSON，请先修正后再切到可视化编辑', 'error')
        return false
    }
    parsedSchema.value = parsed
    visualParams.value = parsed.params.map((param) => ({ ...param, key: nextVisualParamKey() }))
    return true
}

const buildSchemaJson = (): string => serializeToolSchema({
    shape: parsedSchema.value.shape,
    hadRequiredList: parsedSchema.value.hadRequiredList,
    rest: parsedSchema.value.rest,
    // 剥掉仅用于 v-for 的 key：它不属于 schema 数据
    params: visualParams.value.map(({ key, ...param }) => param),
})

const addVisualParam = () => {
    visualParams.value = [...visualParams.value, {
        key: nextVisualParamKey(),
        name: '',
        type: 'string',
        required: true,
        description: '',
        defaultValue: '',
        extra: {},
    }]
}

const removeVisualParam = (index: number) => {
    visualParams.value = visualParams.value.filter((_, i) => i !== index)
}

const switchSchemaMode = (mode: 'json' | 'visual') => {
    if (mode === schemaEditorMode.value) return
    if (mode === 'visual') {
        // JSON 非法时留在原地：既不丢用户输入，也能让他看到错在哪
        if (!loadVisualFromJson()) return
        schemaEditorMode.value = 'visual'
        return
    }
    toolForm.value.parameter_schema_str = buildSchemaJson()
    schemaEditorMode.value = 'json'
}

const cloneTool = (tool: SysApiTool) => {
    openModal(tool, true)
}

const saveTool = async () => {
    if (!toolForm.value.name || !toolForm.value.url_template) {
        showToast('请填写名称和 URL 模板', 'warning')
        return
    }
    
    try {
        let headers = {}
        let schema = {}
        
        try {
            headers = JSON.parse(toolForm.value.headers_str || '{}')
        } catch (e) {
            showToast('Headers 格式错误 (非 JSON)', 'error')
            return
        }
        
        try {
            schema = JSON.parse(toolForm.value.parameter_schema_str || '{}')
        } catch (e) {
            showToast('参数定义格式错误 (非 JSON)', 'error')
            return
        }
        
        const payload: any = {
            name: toolForm.value.name,
            description: toolForm.value.description,
            method: toolForm.value.method,
            url_template: toolForm.value.url_template,
            headers: headers,
            parameter_schema: schema,
            is_active: toolForm.value.is_active,
            group_name: (toolForm.value.group_name || '').trim() || null
        }

        if (isEditing.value && toolForm.value.id) {
            await toolApi.update(toolForm.value.id, payload)
            showToast('更新成功', 'success')
        } else {
            await toolApi.create(payload)
            showToast('创建成功', 'success')
        }
        showModal.value = false
        fetchTools()
    } catch (e: any) {
        showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
}

const deleteTool = (tool: SysApiTool) => {
    deletingTool.value = tool
    showDeleteConfirm.value = true
}

const confirmDeleteTool = async () => {
    const tool = deletingTool.value
    if (!tool) return
    try {
        await toolApi.delete(tool.id)
        showToast('已删除', 'success')
        fetchTools()
    } catch(e: any) {
        showToast('删除失败', 'error')
    } finally {
        showDeleteConfirm.value = false
        deletingTool.value = null
    }
}

defineExpose({ refresh: fetchTools })

onMounted(() => {
  fetchTools()
})
</script>

<template>
  <div class="h-full min-h-0 overflow-y-auto pb-6 p-1 registry-scroll">
      <div class="bg-white shadow rounded-lg overflow-hidden">
         <div class="p-4 border-b border-gray-100 space-y-3">
            <!-- 第一行：标题与主操作分开，标题永不折行 -->
            <div class="flex items-center justify-between gap-3">
                <h3 class="text-lg font-medium text-gray-900 whitespace-nowrap">API 工具注册表</h3>
                <button
                    v-if="canSave"
                    @click="openModal()"
                    class="shrink-0 px-3 py-2 bg-primary text-white text-sm rounded-md hover:bg-primary-dark transition-colors"
                >
                    + 添加工具
                </button>
            </div>
            <!-- 第二行：筛选与视图控件独占一行（搜索框自适应吃掉剩余宽度），不再挤标题 -->
            <div class="flex flex-wrap items-center gap-2">
                <input
                    v-model="toolSearchQuery"
                    type="search"
                    placeholder="搜索名称、描述或 URL..."
                    class="min-w-[12rem] flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <select v-model="toolMethodFilter" class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按 HTTP 方法筛选">
                    <option value="all">Method：全部</option>
                    <option v-for="method in toolMethodOptions" :key="method" :value="method">{{ method }}</option>
                </select>
                <select v-model="toolStatusFilter" class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按状态筛选">
                    <option value="all">状态：全部</option>
                    <option value="active">启用</option>
                    <option value="inactive">停用</option>
                </select>
                <select v-model="groupFilter" class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按业务分组筛选">
                    <option value="all">分组：全部 ({{ groupFilterOptions.all }})</option>
                    <option :value="UNGROUPED_FILTER">未分组 ({{ groupFilterOptions.ungrouped }})</option>
                    <option v-for="[name, count] in groupFilterOptions.groups" :key="name" :value="name">{{ name }} ({{ count }})</option>
                </select>
                <button
                    v-if="canSave"
                    type="button"
                    class="shrink-0 px-2.5 py-2 text-sm rounded-lg border border-gray-300 bg-white shadow-sm hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                    :disabled="filteredTools.length === 0"
                    :title="allFilteredSelected ? '取消选择当前筛选结果' : '选中当前筛选结果（便于批量归类）'"
                    @click="toggleSelectAllFiltered"
                >{{ allFilteredSelected ? '取消选择' : '选中当前筛选结果' }}</button>
                <button
                    v-if="canSave && selectedToolIds.size > 0"
                    type="button"
                    class="shrink-0 px-3 py-2 text-sm rounded-lg bg-primary text-white hover:bg-primary-dark transition-colors"
                    @click="openBatchGroupModal"
                >批量设置分组（{{ selectedToolIds.size }}）</button>
                <div class="shrink-0 flex bg-gray-100 p-0.5 rounded-lg text-xs" role="group" aria-label="视图切换">
                    <button
                        type="button"
                        class="px-2.5 py-1.5 rounded-md transition-all font-medium"
                        :class="viewMode === 'list' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                        @click="viewMode = 'list'"
                    >列表视图</button>
                    <button
                        type="button"
                        class="px-2.5 py-1.5 rounded-md transition-all font-medium"
                        :class="viewMode === 'group' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                        @click="viewMode = 'group'"
                    >分组视图</button>
                </div>
                <button
                    type="button"
                    class="shrink-0 px-2.5 py-2 text-sm text-gray-500 hover:text-primary"
                    :class="{ invisible: !hasToolFilters }"
                    :disabled="!hasToolFilters"
                    :tabindex="hasToolFilters ? 0 : -1"
                    @click="clearToolFilters"
                >清空</button>
            </div>
         </div>
         
         <div
            v-if="canSave && ungroupedToolsCount > 0 && groupFilter !== UNGROUPED_FILTER"
            class="flex flex-wrap items-center gap-2 border-b border-amber-100 bg-amber-50/70 px-4 py-2 text-xs text-amber-800"
         >
            <span>还有 <strong>{{ ungroupedToolsCount }}</strong> 个工具未分组——未分组的工具在智能体配置里会落进「其他扩展工具」</span>
            <button
                type="button"
                class="rounded-md border border-amber-200 bg-white px-2 py-0.5 font-medium hover:bg-amber-100"
                @click="groupFilter = UNGROUPED_FILTER"
            >筛出并批量归类</button>
         </div>
         <div v-if="loading" class="p-8 text-center text-gray-400">加载中...</div>
         <div v-else class="overflow-x-auto">
         <div v-for="group in displayGroups" :key="group.key" :class="viewMode === 'group' ? 'border-b border-gray-100 last:border-b-0' : ''">
            <div v-if="viewMode === 'group'" class="flex items-center gap-2 bg-gray-50/80 px-4 py-2">
                <button
                    type="button"
                    class="flex items-center gap-2 text-left"
                    :aria-expanded="!isGroupCollapsed(group.key)"
                    :aria-label="`${isGroupCollapsed(group.key) ? '展开' : '折叠'}分组 ${group.label}`"
                    @click="toggleRegistryGroupCollapse(group.key)"
                >
                    <svg class="h-3.5 w-3.5 text-gray-400 transition-transform" :class="{ 'rotate-90': !isGroupCollapsed(group.key) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="text-sm font-semibold text-gray-700">{{ group.label }}</span>
                    <span class="text-xs text-gray-400">({{ group.tools.length }})</span>
                </button>
                <span v-if="canSave" class="text-xs text-gray-400">已选 {{ getGroupSelectedCount(group.tools) }}</span>
                <button
                    v-if="canSave"
                    type="button"
                    class="ml-auto text-xs font-medium text-primary hover:underline"
                    @click="selectGroupTools(group)"
                >{{ isGroupAllSelected(group) ? '取消本组' : '全选本组' }}</button>
            </div>
         <table v-show="!isGroupCollapsed(group.key)" class="min-w-[900px] w-full table-fixed divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th class="w-12 px-4 py-3"><span class="sr-only">选择</span>
                        <input
                            v-if="canSave"
                            type="checkbox"
                            class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded"
                            :checked="allFilteredSelected"
                            :disabled="filteredTools.length === 0"
                            aria-label="选中当前筛选结果"
                            title="选中当前筛选结果"
                            @change="toggleSelectAllFiltered"
                        />
                    </th>
                    <th class="w-[40%] min-w-[16rem] px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">名称</th>
                    <th class="w-[10%] px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">Method</th>
                    <th class="w-[13%] px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">业务分组</th>
                    <th class="w-[9%] px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">状态</th>
                    <th class="w-[12%] px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">操作</th>
                </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
                <tr v-for="t in group.tools" :key="t.id" class="hover:bg-gray-50">
                    <td class="px-4 py-4">
                        <input
                            v-if="canSave"
                            type="checkbox"
                            class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded"
                            :checked="selectedToolIds.has(t.id)"
                            :aria-label="`选择工具 ${t.name}`"
                            @change="toggleToolSelection(t.id)"
                        />
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                        <div class="truncate">{{ t.name }}</div>
                        <p class="text-xs text-gray-500 font-normal whitespace-normal break-words">{{ t.description }}</p>
                        <p class="text-xs text-gray-400 font-mono font-normal break-all select-all" :title="t.url_template">{{ t.url_template }}</p>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <span class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-semibold rounded-full" 
                            :class="{'bg-green-100 text-green-800': t.method === 'GET', 'bg-blue-100 text-blue-800': t.method === 'POST', 'bg-yellow-100 text-yellow-800': t.method === 'PUT', 'bg-red-100 text-red-800': t.method === 'DELETE'}"
                        >
                            {{ t.method }}
                        </span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm">
                        <button
                            v-if="t.group_name"
                            type="button"
                            class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-medium rounded-full bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                            :title="`只看「${t.group_name}」的工具`"
                            :aria-label="`只看「${t.group_name}」的工具`"
                            @click="filterByGroup(t.group_name)"
                        >{{ t.group_name }}</button>
                        <span v-else class="text-gray-300">—</span>
                    </td>
                     <td class="px-6 py-4 whitespace-nowrap">
                        <span v-if="t.is_active" class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">启用</span>
                        <span v-else class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">停用</span>
                     </td>
                     <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                        <div v-if="canSave" class="flex items-center justify-end space-x-2">
                            <button 
                                @click="openModal(t)" 
                                title="编辑"
                                class="p-1.5 text-primary hover:bg-blue-50 rounded-md transition-colors"
                            >
                                <PencilSquareIcon class="h-4 w-4" />
                            </button>

                            <button 
                                @click="cloneTool(t)" 
                                title="复制"
                                class="p-1.5 text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
                            >
                                <DocumentDuplicateIcon class="h-4 w-4" />
                            </button>
                            
                            <button 
                                @click="deleteTool(t)" 
                                title="删除"
                                class="p-1.5 text-red-500 hover:bg-red-50 rounded-md transition-colors"
                            >
                                <TrashIcon class="h-4 w-4" />
                            </button>
                        </div>
                        <span v-else class="text-gray-400 italic text-xs">仅限管理</span>
                     </td>
                </tr>
                <tr v-if="group.tools.length === 0">
                    <td colspan="6" class="px-6 py-8 text-center text-gray-400 text-sm">{{ hasToolFilters ? '暂无匹配工具' : '暂无工具配置' }}</td>
                </tr>
            </tbody>
         </table>
         </div>
         </div>
      </div>

      <datalist id="tool-group-options">
          <option v-for="name in existingGroupNames" :key="name" :value="name"></option>
      </datalist>

      <!-- Modal -->
      <Teleport to="body">
        <div v-if="showModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
            <div class="bg-white rounded-xl shadow-xl max-w-3xl w-full p-6 space-y-4 text-left max-h-[90vh] overflow-y-auto custom-scrollbar">
                <h3 class="text-lg font-bold text-gray-900">{{ isEditing ? '编辑工具' : '添加新工具' }}</h3>
              
                <div class="space-y-4">
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                           <label class="block text-sm font-medium text-gray-700">工具名称 (唯一标识)</label>
                           <input v-model="toolForm.name" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="e.g. search_weather" />
                        </div>
                        <div>
                           <label class="block text-sm font-medium text-gray-700">HTTP 方法</label>
                           <select v-model="toolForm.method" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                               <option value="GET">GET</option>
                               <option value="POST">POST</option>
                               <option value="PUT">PUT</option>
                               <option value="DELETE">DELETE</option>
                           </select>
                        </div>
                    </div>
                  
                    <div>
                       <label class="block text-sm font-medium text-gray-700">URL 模板</label>
                       <input v-model="toolForm.url_template" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono" placeholder="https://api.example.com/v1/weather?city={city}" />
                       <p class="text-xs text-gray-500 mt-1">使用 {param} 标记需要替换的参数</p>
                    </div>
                  
                    <div>
                       <label class="block text-sm font-medium text-gray-700">描述</label>
                       <textarea v-model="toolForm.description" rows="2" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="描述工具的功能..."></textarea>
                    </div>
                  
                    <div>
                       <div class="flex flex-wrap items-center gap-2">
                          <label class="block text-sm font-medium text-gray-700">参数定义 (JSON Schema)</label>
                          <div class="flex bg-gray-100 p-0.5 rounded-lg text-xs" role="group" aria-label="参数定义编辑模式">
                             <button
                                type="button"
                                class="px-2.5 py-1 rounded-md transition-all font-medium"
                                :class="schemaEditorMode === 'json' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                                @click="switchSchemaMode('json')"
                             >JSON</button>
                             <button
                                type="button"
                                class="px-2.5 py-1 rounded-md transition-all font-medium"
                                :class="schemaEditorMode === 'visual' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                                @click="switchSchemaMode('visual')"
                             >可视化</button>
                          </div>
                          <span v-if="schemaEditorMode === 'visual' && visualNestedCount > 0" class="text-xs text-amber-600">
                             有 {{ visualNestedCount }} 个嵌套参数：可改类型/描述/必填，子结构请用 JSON 模式编辑
                          </span>
                       </div>
                       <textarea v-if="schemaEditorMode === 'json'" v-model="toolForm.parameter_schema_str" rows="5" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono text-xs bg-white disabled:bg-gray-100" placeholder='{ "city": { "type": "string", "description": "城市名" } }'></textarea>
                       <div v-else class="mt-1 space-y-2 rounded-md border border-gray-200 bg-gray-50/60 p-2">
                          <p v-if="visualParams.length === 0" class="px-1 py-1 text-xs text-gray-400">还没有参数，点下面的「+ 添加参数」开始</p>
                          <div v-for="(p, index) in visualParams" :key="p.key" class="flex flex-wrap items-center gap-2 rounded-md bg-white p-2 shadow-sm">
                             <input v-model="p.name" class="w-40 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono" placeholder="参数名" />
                             <select v-model="p.type" class="w-24 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono">
                                <option v-for="option in PARAM_TYPE_OPTIONS" :key="option" :value="option">{{ option }}</option>
                             </select>
                             <label class="flex items-center gap-1 text-xs text-gray-600">
                                <input type="checkbox" v-model="p.required" class="h-3.5 w-3.5 text-primary focus:ring-primary border-gray-300 rounded" />
                                必填
                             </label>
                             <input v-model="p.description" class="min-w-[10rem] flex-1 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs" placeholder="描述（给 LLM 看）" />
                             <input v-model="p.defaultValue" :disabled="p.required" class="w-32 border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-xs font-mono disabled:bg-gray-100" :placeholder="p.required ? '默认值（需非必填）' : '默认值'" />
                             <span v-if="p.extra.items || p.extra.properties" class="rounded-full bg-amber-50 px-2 text-xs leading-5 text-amber-700" title="嵌套子结构请用 JSON 模式编辑">嵌套</span>
                             <button
                                type="button"
                                class="rounded-md p-1 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                                :title="`删除参数 ${p.name || index + 1}`"
                                :aria-label="`删除参数 ${p.name || index + 1}`"
                                @click="removeVisualParam(index)"
                             >
                                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                   <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                             </button>
                          </div>
                          <button
                             type="button"
                             class="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                             @click="addVisualParam"
                          >+ 添加参数</button>
                          <p class="text-xs text-gray-500">未勾选「必填」的参数会写成 <code class="font-mono">required: false</code>（运行时缺省按必填处理，默认值只在非必填时生效）</p>
                       </div>
                       <p class="text-xs text-gray-500 mt-1">定义参数类型和描述，用于 LLM 理解</p>
                    </div>
                  
                    <div>
                       <label class="block text-sm font-medium text-gray-700">业务分组</label>
                       <input v-model="toolForm.group_name" list="tool-group-options" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="e.g. 履约罚款（留空表示未分组）" />
                       <p class="text-xs text-gray-500 mt-1">智能体配置的「工具能力」步骤会按它归组；留空则该工具落在「其他扩展工具」</p>
                    </div>

                    <div>
                       <label class="block text-sm font-medium text-gray-700">Headers (JSON)</label>
                       <textarea v-model="toolForm.headers_str" rows="3" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono text-xs bg-white disabled:bg-gray-100" placeholder='{ "Authorization": "Bearer token" }'></textarea>
                    </div>

                    <div class="flex items-center">
                        <input id="is_active" type="checkbox" v-model="toolForm.is_active" class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded" />
                        <label for="is_active" class="ml-2 block text-sm text-gray-900">启用此工具</label>
                    </div>
                </div>
              
                <div class="flex justify-end space-x-3 mt-6">
                    <button @click="showModal = false" class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">取消</button>
                    <button @click="saveTool" class="px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark">保存</button>
                </div>
            </div>
        </div>
      </Teleport>

      <Teleport to="body">
        <div v-if="showBatchGroupModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
            <div class="bg-white rounded-xl shadow-xl max-w-md w-full p-6 space-y-4 text-left">
                <h3 class="text-lg font-bold text-gray-900">批量设置业务分组</h3>
                <p class="text-sm text-gray-500">将对已选中的 {{ selectedToolIds.size }} 个工具设置业务分组；留空表示清除分组。</p>
                <div>
                    <label class="block text-sm font-medium text-gray-700">业务分组</label>
                    <input v-model="batchGroupName" list="tool-group-options" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="e.g. 履约罚款（留空表示清除分组）" />
                </div>
                <div class="flex justify-end space-x-3 pt-2">
                    <button @click="showBatchGroupModal = false" class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">取消</button>
                    <button @click="confirmBatchGroup" class="px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark">确定</button>
                </div>
            </div>
        </div>
      </Teleport>

      <ConfirmModal
          v-if="showDeleteConfirm"
          title="确认删除工具"
          :message="`确定要删除工具「${deletingTool?.name || ''}」吗？删除后无法恢复。`"
          confirm-text="确认删除"
          @confirm="confirmDeleteTool"
          @cancel="showDeleteConfirm = false; deletingTool = null"
      />
  </div>
</template>

<style scoped>
.registry-scroll {
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: rgba(156, 163, 175, 0.3) transparent;
}
.registry-scroll::-webkit-scrollbar { width: 6px; }
.registry-scroll::-webkit-scrollbar-track { background: transparent; }
.registry-scroll::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.3); border-radius: 3px; }
</style>
