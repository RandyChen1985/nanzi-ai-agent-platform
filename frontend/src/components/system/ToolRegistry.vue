<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick, watch } from 'vue'
import { toolApi, scriptToolApi, type SysApiTool } from '../../api/tool'
import { SUPPORTED_HTTP_METHODS } from '../../utils/toolMethods'
import { parseToolSchema, validateSystemParams } from '../../utils/toolSchema'
import { isValidSigningKeyId, isValidSigningSecret } from '../../utils/requestSigning'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import ConfirmModal from '../ConfirmModal.vue'
import ApiToolTester from './ApiToolTester.vue'
import ToolHeadersEditor from './ToolHeadersEditor.vue'
import ToolParamEditor from './ToolParamEditor.vue'
import GroupSelect from './GroupSelect.vue'
import ToolWizard from './ToolWizard.vue'
import ToolSigningEditor from './ToolSigningEditor.vue'
import ScriptToolList from './ScriptToolList.vue'
import ToolBundleImportModal from './ToolBundleImportModal.vue'
import { useToolBundleExport } from '../../composables/useToolBundleExport'
import { pendingCredentialHeaders } from '../../utils/toolBundle'
import { 
  PencilSquareIcon,
  TrashIcon,
  DocumentDuplicateIcon,
  PlayIcon
} from '@heroicons/vue/24/outline'

const { showToast } = useToast()
const { hasPermission } = useUser()
const canSave = hasPermission('element:system:config_save')
// 工具类型 tab：API 工具 / 脚本工具（两者数据源、表单、执行位置都不同）
const activeToolType = ref<'api' | 'script'>('api')
const scriptToolCount = ref(0)

const tools = ref<SysApiTool[]>([])
const loading = ref(false)
const toolSearchQuery = ref('')
const toolMethodFilter = ref('all')
const toolStatusFilter = ref('all')
const showModal = ref(false)
const isEditing = ref(false)
const showDeleteConfirm = ref(false)
const deletingTool = ref<SysApiTool | null>(null)
const showDiscardConfirm = ref(false)
const saving = ref(false)
const formError = ref('')
const fieldErrors = ref<Partial<Record<'name' | 'url_template' | 'headers_str' | 'parameter_schema_str' | 'signing', string>>>({})
const suppressFormWatch = ref(false)
const wizardDirty = ref(false)
const initialFormSnapshot = ref('')
const groupFilter = ref('all')
const selectedToolIds = ref<Set<string>>(new Set())
const showBatchGroupModal = ref(false)
const batchGroupName = ref('')
/** 筛选面板开合（Method/状态/分组收进面板，参考用户管理页） */
const showToolFilterPanel = ref(false)
const showGroupManager = ref(false)
const groupManagerSource = ref('')
const groupManagerTarget = ref('')
const groupManagerSaving = ref(false)
const showGroupClearConfirm = ref(false)
const quickTestTool = ref<SysApiTool | null>(null)
const advancedOpen = ref(false)
const toggleTarget = ref<{ tool: SysApiTool; active: boolean } | null>(null)
const toggleSaving = ref(false)
// 筛选下拉里代表「没有业务分组」的哨兵值
const UNGROUPED_FILTER = '__ungrouped__'
// 列表视图（扁平行）/ 分组视图（按业务分组折叠）
const viewMode = ref<'list' | 'group'>('list')
const collapsedRegistryGroups = ref<Set<string>>(new Set())

type ToolForm = {
  id?: string
  name: string
  description: string
  method: string
  url_template: string
  headers_str: string
  parameter_schema_str: string
  signing_enabled: boolean
  signing_key_id: string | null
  signing_algorithm: string
  signing_secret: string
  signing_secret_configured: boolean
  is_active: boolean
  group_name: string
}

const toolForm = ref<ToolForm>({
  name: '',
  description: '',
  method: 'GET',
  url_template: '',
  headers_str: '{}',
  parameter_schema_str: '{}',
  signing_enabled: false,
  signing_key_id: null,
  signing_algorithm: 'hmac-sha256',
  signing_secret: '',
  signing_secret_configured: false,
  is_active: true,
  group_name: ''
})

const snapshotForm = () => JSON.stringify(toolForm.value)

const isDirty = computed(() => showModal.value && (
    wizardDirty.value || snapshotForm() !== initialFormSnapshot.value
))

const clearFormErrors = () => {
    formError.value = ''
    fieldErrors.value = {}
}

const fieldElement = (key: string): HTMLElement | null => (
    document.querySelector(`[data-tool-field="${key}"]`) as HTMLElement | null
)

const focusField = async (key: string) => {
    if (key === 'headers_str' || key === 'signing') advancedOpen.value = true
    await nextTick()
    const element = fieldElement(key)
    element?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    element?.focus()
}

const requestCloseModal = () => {
    if (saving.value) return
    if (isDirty.value) {
        showDiscardConfirm.value = true
        return
    }
    showModal.value = false
}

const confirmDiscard = () => {
    showDiscardConfirm.value = false
    showModal.value = false
    clearFormErrors()
}

const handleBeforeUnload = (event: BeforeUnloadEvent) => {
    if (!isDirty.value) return
    event.preventDefault()
    event.returnValue = ''
}

// 用户重新编辑时收起上一轮的行内错误；保存/校验期间不要清掉刚设置的错误。
watch(toolForm, () => {
    if (!suppressFormWatch.value) clearFormErrors()
}, { deep: true })

const headerCount = computed(() => {
    try {
        const parsed = JSON.parse(toolForm.value.headers_str || '{}')
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? Object.keys(parsed).length
            : 0
    } catch {
        return 0
    }
})

const urlPlaceholders = computed(() => {
    const names = new Set<string>()
    for (const match of toolForm.value.url_template.matchAll(/\{([^{}]+)\}/g)) {
        const name = match[1]
        if (name) names.add(name)
    }
    return Array.from(names)
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

// 静态项来自共享清单（不再各写一份）；
// 再并上「既有数据里真正出现过的方法」——后端 method 无枚举校验，
// 历史库里的异常值（如 HEAD）若不在选项里就再也筛不出来。
const toolMethodOptions = computed(() => Array.from(new Set<string>([
    ...SUPPORTED_HTTP_METHODS,
    ...tools.value.map((tool) => String(tool.method || '').toUpperCase()).filter(Boolean),
])))

const activeToolFilterCount = computed(() => {
    let count = 0
    if (toolMethodFilter.value !== 'all') count += 1
    if (toolStatusFilter.value !== 'all') count += 1
    if (groupFilter.value !== 'all') count += 1
    return count
})

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

// 导出范围：有勾选就导勾选，没勾选就导当前筛选结果（与「批量设置分组」同一套选择语义）
const exportApiIds = computed(() => (
    selectedToolIds.value.size
        ? Array.from(selectedToolIds.value)
        : filteredTools.value.map((tool) => tool.id)
))

const { exporting: exportingBundle, exportBundle: runExportBundle } = useToolBundleExport()

const exportApiTools = async () => {
    await runExportBundle('api', exportApiIds.value)
}

const showBundleImport = ref(false)
/** 「+ 添加工具」右侧的下拉（导入 / 导出 / 分组管理） */
const showToolActionsMenu = ref(false)

function closeToolActionsMenu() {
  showToolActionsMenu.value = false
}

// 导入成功后两类工具都可能变化：API 列表由本组件持有，脚本计数在子组件里
const handleBundleImported = async () => {
    await fetchTools()
    await loadScriptToolCount?.()
}

/** 行内「待补凭据」：headers 里还是 ${CREDENTIAL:...} 占位符 */
const toolPendingCredentials = (tool: SysApiTool) => pendingCredentialHeaders(tool.headers)

// 弹窗内的两种填写方式：表单模式是全字段平铺（原有交互），向导模式是 4 步引导。
// 两者**共用同一份 toolForm**——向导只是填它的另一种交互，所以切 tab 不丢内容，
// saveTool() 也仍然只有一条保存路径。
const editorMode = ref<'form' | 'wizard'>('form')

const openModal = (tool?: SysApiTool, isClone = false) => {
    if (tool) {
        isEditing.value = !isClone
        // If clone, reset ID and append copy to name
        const initialForm: ToolForm = {
            id: isClone ? undefined : tool.id,
            name: isClone ? `${tool.name}_copy` : tool.name,
            description: tool.description || '',
            method: tool.method || 'GET',
            url_template: tool.url_template || '',
            headers_str: JSON.stringify(tool.headers || {}, null, 2),
            parameter_schema_str: JSON.stringify(tool.parameter_schema || {}, null, 2),
            signing_enabled: !!tool.signing_enabled,
            // 密钥永远不回显：编辑时留空表示沿用，克隆时必须重新生成
            signing_secret: '',
            signing_key_id: isClone ? null : (tool.signing_key_id || null),
            signing_algorithm: tool.signing_algorithm || 'hmac-sha256',
            signing_secret_configured: isClone ? false : !!tool.signing_secret_configured,
            is_active: !!tool.is_active,
            group_name: tool.group_name || '',
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
            signing_enabled: false,
            signing_key_id: null,
            signing_algorithm: 'hmac-sha256',
            signing_secret: '',
            signing_secret_configured: false,
            is_active: true,
            group_name: ''
        }
    }
    // 新建优先走向导；编辑已有工具走完整表单，避免用户每次都被迫从第 1 步开始。
    editorMode.value = tool && !isClone ? 'form' : 'wizard'
    clearFormErrors()
    advancedOpen.value = false
    wizardDirty.value = false
    suppressFormWatch.value = false
    initialFormSnapshot.value = snapshotForm()
    showModal.value = true
}

const cloneTool = (tool: SysApiTool) => {
    openModal(tool, true)
}

const validateForm = (): { headers: Record<string, any>; schema: Record<string, any> } | null => {
    clearFormErrors()
    const name = toolForm.value.name.trim()
    const urlTemplate = toolForm.value.url_template.trim()
    if (!name) {
        fieldErrors.value.name = '请填写工具名称'
        formError.value = editorMode.value === 'wizard'
            ? '请先在向导第 2 步填写工具名称，再保存。'
            : '请先修正表单里的错误，再保存。'
        if (editorMode.value !== 'wizard') void focusField('name')
        return null
    }
    if (!urlTemplate) {
        fieldErrors.value.url_template = '请填写 URL 模板'
        formError.value = editorMode.value === 'wizard'
            ? '请先在向导第 1 步粘贴请求地址并解析，再保存。'
            : '请先修正表单里的错误，再保存。'
        if (editorMode.value !== 'wizard') void focusField('url_template')
        return null
    }
    toolForm.value.name = name
    toolForm.value.url_template = urlTemplate

    let headers: Record<string, any>
    try {
        const parsed = JSON.parse(toolForm.value.headers_str || '{}')
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('Headers 必须是 JSON 对象')
        }
        headers = parsed
    } catch (e: any) {
        fieldErrors.value.headers_str = e?.message?.includes('必须是 JSON 对象')
            ? e.message
            : 'Headers 格式错误（非 JSON）'
        formError.value = 'Headers 配置有误，请先修正。'
        void focusField('headers_str')
        return null
    }

    let schema: Record<string, any>
    try {
        const parsed = JSON.parse(toolForm.value.parameter_schema_str || '{}')
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('参数定义必须是 JSON 对象')
        }
        schema = parsed
    } catch (e: any) {
        fieldErrors.value.parameter_schema_str = e?.message?.includes('必须是 JSON 对象')
            ? e.message
            : '参数定义格式错误（非 JSON）'
        formError.value = '参数定义有误，请先修正。'
        void focusField('parameter_schema_str')
        return null
    }

    const parsedSchema = parseToolSchema(toolForm.value.parameter_schema_str)
    if (parsedSchema) {
        const systemParamErrors = validateSystemParams(
            parsedSchema.systemParams,
            parsedSchema.params,
        )
        const firstError = systemParamErrors[0]
        if (firstError) {
            fieldErrors.value.parameter_schema_str = firstError
            formError.value = firstError
            void focusField('parameter_schema_str')
            return null
        }
    }

    const signingEnabled = !!toolForm.value.signing_enabled
    const signingKeyId = String(toolForm.value.signing_key_id || '').trim()
    const signingSecret = toolForm.value.signing_secret || ''
    if (signingEnabled) {
        if (!isValidSigningKeyId(signingKeyId)) {
            const message = '请求签名已开启：Key-Id 必须是 16 位小写十六进制，请重新生成'
            formError.value = message
            fieldErrors.value.signing = message
            void focusField('signing')
            return null
        }
        if (!signingSecret && !toolForm.value.signing_secret_configured) {
            const message = '请求签名已开启：请先生成或填写签名密钥'
            formError.value = message
            fieldErrors.value.signing = message
            void focusField('signing')
            return null
        }
        if (signingSecret && !isValidSigningSecret(signingSecret)) {
            const message = '签名密钥必须是 32-128 个可打印 ASCII 字符'
            formError.value = message
            fieldErrors.value.signing = message
            void focusField('signing')
            return null
        }
    }
    return { headers, schema }
}

const requestToggleActive = (tool: SysApiTool) => {
    if (toggleSaving.value) return
    toggleTarget.value = { tool, active: !tool.is_active }
}

const confirmToggleActive = async () => {
    const target = toggleTarget.value
    if (!target || toggleSaving.value) return
    toggleSaving.value = true
    try {
        await toolApi.update(target.tool.id, { is_active: target.active })
        target.tool.is_active = target.active
        showToast(target.active ? '工具已启用' : '工具已停用', 'success')
        toggleTarget.value = null
    } catch (e: any) {
        showToast('更新状态失败: ' + (e.response?.data?.detail || e.message), 'error')
    } finally {
        toggleSaving.value = false
    }
}

const groupManagerTools = computed(() => {
    if (!groupManagerSource.value) return []
    if (groupManagerSource.value === UNGROUPED_FILTER) {
        return tools.value.filter((tool) => !String(tool.group_name || '').trim())
    }
    return tools.value.filter((tool) => String(tool.group_name || '').trim() === groupManagerSource.value)
})

const openGroupManager = () => {
    const firstGroup = groupFilterOptions.value.groups[0]?.[0] || ''
    groupManagerSource.value = firstGroup || (groupFilterOptions.value.ungrouped ? UNGROUPED_FILTER : '')
    groupManagerTarget.value = groupManagerSource.value === UNGROUPED_FILTER ? '' : groupManagerSource.value
    showGroupManager.value = true
}

const onGroupManagerSourceChange = () => {
    groupManagerTarget.value = groupManagerSource.value === UNGROUPED_FILTER ? '' : groupManagerSource.value
}

const applyGroupChange = async (groupName: string | null) => {
    if (groupManagerSaving.value) return
    const ids = groupManagerTools.value.map((tool) => tool.id)
    if (ids.length === 0) return
    groupManagerSaving.value = true
    try {
        await toolApi.batchSetGroup(ids, groupName)
        showToast(groupName ? `已更新 ${ids.length} 个工具的分组` : `已清空 ${ids.length} 个工具的分组`, 'success')
        showGroupManager.value = false
        showGroupClearConfirm.value = false
        fetchTools()
    } catch (e: any) {
        showToast('更新分组失败: ' + (e.response?.data?.detail || e.message), 'error')
    } finally {
        groupManagerSaving.value = false
    }
}

const renameOrMergeGroup = () => {
    const target = groupManagerTarget.value.trim()
    if (!target) {
        showToast('请填写新的分组名称，留空请使用「清空分组」', 'warning')
        return
    }
    if (target === groupManagerSource.value) {
        showToast('分组名称没有变化', 'warning')
        return
    }
    void applyGroupChange(target)
}

const clearSelectedGroup = () => {
    showGroupClearConfirm.value = true
}

const saveTool = async () => {
    if (saving.value) return
    saving.value = true
    suppressFormWatch.value = true
    const validated = validateForm()
    if (!validated) {
        saving.value = false
        void nextTick(() => { suppressFormWatch.value = false })
        return
    }

    try {
        const signingEnabled = !!toolForm.value.signing_enabled
        const signingKeyId = String(toolForm.value.signing_key_id || '').trim()
        const signingSecret = toolForm.value.signing_secret || ''
        const payload: any = {
            name: toolForm.value.name,
            description: toolForm.value.description,
            method: toolForm.value.method,
            url_template: toolForm.value.url_template,
            headers: validated.headers,
            parameter_schema: validated.schema,
            signing_enabled: signingEnabled,
            ...(signingEnabled ? {
                signing_key_id: signingKeyId,
                signing_algorithm: 'hmac-sha256',
            } : {}),
            ...(signingEnabled && signingSecret ? { signing_secret: signingSecret } : {}),
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
        // 密钥已经交给服务端加密保存，前端不再保留明文
        toolForm.value.signing_secret = ''
        if (signingEnabled) toolForm.value.signing_secret_configured = true
        initialFormSnapshot.value = snapshotForm()
        wizardDirty.value = false
        showModal.value = false
        fetchTools()
    } catch (e: any) {
        formError.value = '保存失败: ' + (e.response?.data?.detail || e.message)
        showToast(formError.value, 'error')
    } finally {
        saving.value = false
        void nextTick(() => { suppressFormWatch.value = false })
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

/**
 * 脚本 tab 的计数必须自己先拉一次。
 *
 * 脚本列表是懒挂载的（切到该 tab 才创建），它上报的 count 在首次进入页面前
 * 根本不存在——于是 tab 上会显示 0，切过去才变成真实数量。这种「看着像没数据」
 * 的错觉比多一次请求更糟。
 */
const loadScriptToolCount = async () => {
  try {
    const res = await scriptToolApi.list()
    const rows = Array.isArray(res.data) ? res.data : ((res.data as any)?.data || [])
    scriptToolCount.value = rows.length
  } catch {
    // 拉取失败不影响 API 工具区；切到脚本 tab 时列表组件会重新加载并上报
  }
}

defineExpose({ refresh: fetchTools })

onMounted(() => {
  fetchTools()
  loadScriptToolCount()
  window.addEventListener('beforeunload', handleBeforeUnload)
  // 点页面别处收起下拉；容器上有 @click.stop，点菜单内部不会误关
  document.addEventListener('click', closeToolActionsMenu)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  document.removeEventListener('click', closeToolActionsMenu)
})
</script>

<template>
  <div class="h-full min-h-0 overflow-y-auto pb-6 p-1 registry-scroll">
      <div class="bg-white shadow rounded-lg overflow-hidden">
         <!-- 工具类型：两类工具的形态差别大，用卡片内 tab 分流，而不是塞进一张表 -->
         <div class="flex items-center gap-1 border-b border-gray-100 px-4 pt-3" role="tablist" aria-label="工具类型">
            <button
                type="button"
                role="tab"
                :aria-selected="activeToolType === 'api'"
                class="relative px-3 pb-2.5 text-sm font-medium transition-colors"
                :class="activeToolType === 'api' ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
                @click="activeToolType = 'api'"
            >
                API 工具
                <span class="ml-1.5 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">{{ tools.length }}</span>
                <span v-if="activeToolType === 'api'" class="absolute inset-x-2 -bottom-px h-0.5 rounded-t bg-primary"></span>
            </button>
            <button
                type="button"
                role="tab"
                :aria-selected="activeToolType === 'script'"
                class="relative px-3 pb-2.5 text-sm font-medium transition-colors"
                :class="activeToolType === 'script' ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
                @click="activeToolType = 'script'"
            >
                脚本工具
                <span class="ml-1.5 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">{{ scriptToolCount }}</span>
                <span v-if="activeToolType === 'script'" class="absolute inset-x-2 -bottom-px h-0.5 rounded-t bg-primary"></span>
            </button>
         </div>
         <div v-if="activeToolType === 'api'">
         <div class="p-4 border-b border-gray-100 space-y-3">
            <!-- 第一行只放标题与说明，操作全部下移到搜索行，与「脚本工具」页保持同一版式 -->
            <div>
                <h3 class="text-lg font-medium text-gray-900 whitespace-nowrap">工具注册表</h3>
                <p class="mt-1 text-sm text-gray-500">
                    API 工具：平台代你发 HTTP 请求；导出时凭据只留占位符，导入后需要补自己的值。
                </p>
            </div>
            <!-- 第二行：搜索 + 一个「筛选」入口。三个下拉常驻会把搜索框挤窄、占掉列表首屏，
                 收进面板后与「用户管理」页面保持同一套交互 -->
            <div class="flex flex-wrap items-center gap-2">
                <input
                    v-model="toolSearchQuery"
                    type="search"
                    placeholder="搜索名称、描述或 URL..."
                    class="min-w-[12rem] flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <div class="relative shrink-0" @keydown.esc="showToolFilterPanel = false">
                    <button
                        type="button"
                        class="inline-flex items-center gap-1.5 rounded-lg border bg-white px-3 py-2 text-sm font-medium shadow-sm transition-colors"
                        :class="activeToolFilterCount ? 'border-primary/40 text-gray-800' : 'border-gray-300 text-gray-700 hover:bg-gray-50'"
                        :aria-expanded="showToolFilterPanel"
                        aria-haspopup="true"
                        @click="showToolFilterPanel = !showToolFilterPanel"
                    >
                        <svg class="h-4 w-4" :class="activeToolFilterCount ? 'text-primary' : 'text-gray-400'" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5h18l-7 8v6l-4-2v-4z" />
                        </svg>
                        筛选
                        <span
                            v-if="activeToolFilterCount"
                            class="inline-flex min-w-[1.125rem] items-center justify-center rounded-full bg-primary/10 px-1 text-[10px] font-bold text-primary"
                        >{{ activeToolFilterCount }}</span>
                        <svg class="h-4 w-4 text-gray-400 transition-transform" :class="{ 'rotate-180': showToolFilterPanel }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 9l6 6 6-6" />
                        </svg>
                    </button>

                    <div
                        v-if="showToolFilterPanel"
                        class="absolute right-0 z-30 mt-1.5 w-[min(21rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white p-3 shadow-lg"
                        role="group"
                        aria-label="筛选条件"
                    >
                        <div>
                            <p class="mb-1.5 text-xs font-semibold text-gray-500">HTTP 方法</p>
                            <div class="flex flex-wrap gap-1.5">
                                <button
                                    v-for="opt in ['all', ...toolMethodOptions]"
                                    :key="`m-${opt}`"
                                    type="button"
                                    class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                                    :class="toolMethodFilter === opt
                                        ? 'border-primary bg-primary/10 text-primary'
                                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                                    :aria-pressed="toolMethodFilter === opt"
                                    @click="toolMethodFilter = opt"
                                >{{ opt === 'all' ? '全部' : opt }}</button>
                            </div>
                        </div>

                        <div class="my-3 border-t border-gray-100"></div>

                        <div>
                            <p class="mb-1.5 text-xs font-semibold text-gray-500">状态</p>
                            <div class="flex flex-wrap gap-1.5">
                                <button
                                    v-for="opt in [{ value: 'all', label: '全部' }, { value: 'active', label: '启用' }, { value: 'inactive', label: '停用' }]"
                                    :key="`s-${opt.value}`"
                                    type="button"
                                    class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                                    :class="toolStatusFilter === opt.value
                                        ? 'border-primary bg-primary/10 text-primary'
                                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                                    :aria-pressed="toolStatusFilter === opt.value"
                                    @click="toolStatusFilter = opt.value as any"
                                >{{ opt.label }}</button>
                            </div>
                        </div>

                        <div class="my-3 border-t border-gray-100"></div>

                        <div>
                            <p class="mb-1.5 text-xs font-semibold text-gray-500">业务分组</p>
                            <select
                                v-model="groupFilter"
                                class="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                            >
                                <option value="all">全部分组（{{ groupFilterOptions.all }}）</option>
                                <option :value="UNGROUPED_FILTER">未分组（{{ groupFilterOptions.ungrouped }}）</option>
                                <option v-for="[name, count] in groupFilterOptions.groups" :key="name" :value="name">{{ name }}（{{ count }}）</option>
                            </select>
                        </div>

                        <div class="mt-3 flex items-center justify-between border-t border-gray-100 pt-2.5">
                            <span class="text-[11px] text-gray-400">
                                {{ activeToolFilterCount ? `已选 ${activeToolFilterCount} 项` : '未设置筛选' }}
                            </span>
                            <button
                                type="button"
                                class="text-xs font-medium text-primary transition-colors hover:text-primary-dark disabled:cursor-not-allowed disabled:text-gray-300"
                                :disabled="!activeToolFilterCount"
                                @click="toolMethodFilter = 'all'; toolStatusFilter = 'all'; groupFilter = 'all'"
                            >清除全部</button>
                        </div>
                    </div>
                </div>
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
                <!-- 主操作收进一个下拉：导入/导出与新建同属「往注册表里加东西」，平铺会挤压搜索框 -->
                <div v-if="canSave" class="relative shrink-0" @click.stop>
                    <div class="flex items-stretch overflow-hidden rounded-lg shadow-sm">
                        <button
                            type="button"
                            class="flex items-center gap-1.5 bg-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-dark"
                            @click="showToolActionsMenu = false; openModal()"
                        >+ 添加工具</button>
                        <button
                            type="button"
                            class="border-l border-white/25 bg-primary px-2 text-white transition-colors hover:bg-primary-dark"
                            title="更多操作：导入 / 导出 / 分组管理"
                            @click="showToolActionsMenu = !showToolActionsMenu"
                        >
                            <svg class="h-4 w-4 transition-transform" :class="{ 'rotate-180': showToolActionsMenu }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                            </svg>
                        </button>
                    </div>
                    <div
                        v-if="showToolActionsMenu"
                        class="absolute right-0 top-full z-40 mt-1 w-56 overflow-hidden rounded-lg border border-gray-100 bg-white py-1 shadow-xl"
                    >
                        <button
                            type="button"
                            class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300"
                            :disabled="exportingBundle || exportApiIds.length === 0"
                            :title="exportApiIds.length === 0 ? '没有可导出的工具' : '导出勾选的工具；未勾选时导出当前筛选结果'"
                            @click="showToolActionsMenu = false; exportApiTools()"
                        >
                            <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" />
                            </svg>
                            {{ exportingBundle ? '导出中…' : `导出 JSON（${exportApiIds.length}）` }}
                        </button>
                        <button
                            type="button"
                            class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
                            @click="showToolActionsMenu = false; showBundleImport = true"
                        >
                            <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 20V8m0 0l-4 4m4-4l4 4M4 4h16" />
                            </svg>
                            导入 JSON
                        </button>
                        <div class="my-1 border-t border-gray-100"></div>
                        <button
                            type="button"
                            class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
                            @click="showToolActionsMenu = false; openGroupManager()"
                        >
                            <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h7" />
                            </svg>
                            分组管理
                        </button>
                    </div>
                </div>
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
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">名称</th>
                    <th class="w-24 px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">Method</th>
                    <th class="w-32 px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">业务分组</th>
                    <th class="w-32 px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">状态</th>
                    <th class="w-44 px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">操作</th>
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
                        <div class="flex items-center gap-1.5">
                            <span class="truncate">{{ t.name }}</span>
                            <span
                                v-if="toolPendingCredentials(t).length"
                                class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700"
                                :title="`这些 header 还是占位符，启用前请补真实值：${toolPendingCredentials(t).join('、')}`"
                            >待补凭据</span>
                        </div>
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
                     <td class="w-32 px-6 py-4 whitespace-nowrap">
                        <div class="flex items-center gap-2">
                            <span v-if="t.is_active" class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">启用</span>
                            <span v-else class="px-2 inline-flex whitespace-nowrap text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">停用</span>
                            <button
                                v-if="canSave"
                                type="button"
                                role="switch"
                                :aria-checked="t.is_active"
                                :aria-label="`${t.is_active ? '停用' : '启用'}工具 ${t.name}`"
                                :title="`${t.is_active ? '停用' : '启用'}工具`"
                                class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                                :class="t.is_active ? 'bg-primary' : 'bg-gray-300'"
                                @click="requestToggleActive(t)"
                            >
                                <span
                                    class="inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform"
                                    :class="t.is_active ? 'translate-x-[18px]' : 'translate-x-[3px]'"
                                ></span>
                            </button>
                        </div>
                     </td>
                     <td class="w-44 px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                        <div v-if="canSave" class="flex items-center justify-end space-x-1.5">
                            <button
                                v-if="canSave"
                                type="button"
                                @click="quickTestTool = t"
                                title="试跑"
                                aria-label="试跑工具"
                                class="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded-md transition-colors"
                            >
                                <PlayIcon class="h-4 w-4" />
                            </button>

                            <button 
                                v-if="canSave"
                                type="button"
                                @click="openModal(t)" 
                                title="编辑"
                                aria-label="编辑工具"
                                class="p-1.5 text-primary hover:bg-blue-50 rounded-md transition-colors"
                            >
                                <PencilSquareIcon class="h-4 w-4" />
                            </button>

                            <button 
                                v-if="canSave"
                                type="button"
                                @click="cloneTool(t)" 
                                title="复制"
                                aria-label="复制工具"
                                class="p-1.5 text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
                            >
                                <DocumentDuplicateIcon class="h-4 w-4" />
                            </button>
                            
                            <button 
                                v-if="canSave"
                                type="button"
                                @click="deleteTool(t)" 
                                title="删除"
                                aria-label="删除工具"
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

      <div v-else-if="activeToolType === 'script'">
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
          <div>
            <h3 class="text-lg font-medium text-gray-900 whitespace-nowrap">工具注册表</h3>
            <p class="mt-0.5 text-xs text-gray-500">脚本工具：在会话沙箱内执行，stdin 收 JSON、stdout 出结果</p>
          </div>
        </div>
        <ScriptToolList
            :can-save="canSave"
            @count="scriptToolCount = $event"
            @imported="fetchTools"
        />
      </div>
      </div>

      <!-- Modal -->
      <Teleport to="body">
        <div v-if="showModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
            <div class="bg-white rounded-xl shadow-xl max-w-5xl w-full p-4 sm:p-6 space-y-4 text-left max-h-[90vh] overflow-y-auto custom-scrollbar">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <h3 class="text-lg font-bold text-gray-900">{{ isEditing ? '编辑工具' : '添加新工具' }}</h3>
                    <!-- 两种填写方式：选中态给足对比（白底 + 蓝字 + 蓝描边），未选中压到灰底上 -->
                    <div class="flex rounded-xl bg-gray-200/70 p-1 text-xs ring-1 ring-inset ring-gray-200" role="group" aria-label="填写方式">
                        <button
                            type="button"
                            class="rounded-lg px-3 py-1.5 font-medium transition-all"
                            :class="editorMode === 'form'
                                ? 'bg-white font-semibold text-primary shadow-sm ring-1 ring-primary/40'
                                : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'"
                            :aria-pressed="editorMode === 'form'"
                            @click="editorMode = 'form'"
                        >表单模式</button>
                        <button
                            type="button"
                            class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition-all"
                            :class="editorMode === 'wizard'
                                ? 'bg-white font-semibold text-primary shadow-sm ring-1 ring-primary/40'
                                : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'"
                            :aria-pressed="editorMode === 'wizard'"
                            @click="editorMode = 'wizard'"
                        >向导模式<span
                            class="rounded-full px-1.5 text-[11px] font-semibold"
                            :class="editorMode === 'wizard' ? 'bg-primary/10 text-primary' : 'bg-gray-300/70 text-gray-600'"
                        >推荐小白使用</span></button>
                    </div>
                </div>

                <div v-if="formError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                    {{ formError }}
                </div>
              
                <!-- 向导模式：4 步引导，写的是同一份 toolForm -->
                <ToolWizard
                    v-if="editorMode === 'wizard'"
                    :tool-form="toolForm"
                    @dirty-change="wizardDirty = $event"
                />

                <div v-else class="space-y-4">
                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <div>
                           <label for="tool-name" class="block text-sm font-medium text-gray-700">工具名称 (唯一标识)</label>
                           <input id="tool-name" v-model="toolForm.name" data-tool-field="name" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="e.g. search_weather" />
                           <p v-if="fieldErrors.name" class="mt-1 text-xs text-red-600">{{ fieldErrors.name }}</p>
                        </div>
                        <div>
                           <label for="tool-method" class="block text-sm font-medium text-gray-700">HTTP 方法</label>
                           <select id="tool-method" v-model="toolForm.method" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                               <option v-for="method in SUPPORTED_HTTP_METHODS" :key="method" :value="method">{{ method }}</option>
                           </select>
                        </div>
                    </div>
                  
                    <div>
                       <label for="tool-url-template" class="block text-sm font-medium text-gray-700">URL 模板</label>
                       <input id="tool-url-template" v-model="toolForm.url_template" data-tool-field="url_template" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono" placeholder="https://api.example.com/v1/weather?city={city}" />
                       <p v-if="fieldErrors.url_template" class="mt-1 text-xs text-red-600">{{ fieldErrors.url_template }}</p>
                       <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
                           <p class="text-xs text-gray-500">使用 {param} 标记需要替换的参数；路径中的参数最终会做路径替换，其余参数按方法落到 query/body。</p>
                           <button type="button" class="shrink-0 text-xs font-medium text-primary hover:underline" @click="editorMode = 'wizard'">从请求重新识别参数</button>
                       </div>
                       <div v-if="urlPlaceholders.length" class="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-gray-500">
                           <span>地址中的占位符：</span>
                           <code v-for="name in urlPlaceholders" :key="name" class="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-blue-700">{{ '{' + name + '}' }}</code>
                       </div>
                    </div>
                  
                    <div>
                       <label for="tool-description" class="block text-sm font-medium text-gray-700">描述</label>
                       <textarea id="tool-description" v-model="toolForm.description" rows="2" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="描述工具的功能..."></textarea>
                       <p class="text-xs text-gray-500 mt-1">这段描述会直接交给模型，用来判断「什么场景该调用它」。</p>
                    </div>

                    <div data-tool-field="parameter_schema_str" class="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary/30" tabindex="-1">
                        <ToolParamEditor
                            v-model="toolForm.parameter_schema_str"
                            :method="toolForm.method"
                            :url-template="toolForm.url_template"
                        />
                        <p v-if="fieldErrors.parameter_schema_str" class="mt-1 text-xs text-red-600">{{ fieldErrors.parameter_schema_str }}</p>
                    </div>

                    <div>
                       <label for="tool-group" class="block text-sm font-medium text-gray-700">业务分组</label>
                       <GroupSelect
                         id="tool-group"
                         v-model="toolForm.group_name"
                         class="mt-1"
                         :options="groupFilterOptions.groups.map(([name, count]) => ({ name, count }))"
                         placeholder="选择已有分组，或直接输入新分组（留空 = 未分组）"
                       />
                       <p class="text-xs text-gray-500 mt-1">智能体配置的「工具能力」步骤会按它归组；留空则该工具落在「其他扩展工具」</p>
                    </div>

                    <details
                        :open="advancedOpen"
                        class="rounded-lg border border-gray-200 bg-gray-50/40"
                        @toggle="advancedOpen = ($event.target as HTMLDetailsElement).open"
                    >
                        <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">
                            <span>高级请求配置：Headers 与请求签名</span>
                            <span v-if="headerCount" class="ml-2 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">{{ headerCount }} 个 Header</span>
                            <span v-if="toolForm.signing_enabled" class="ml-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">签名已开启</span>
                            <span class="ml-1 text-xs font-normal text-gray-400">需要鉴权或验签时展开</span>
                        </summary>
                        <div class="space-y-4 border-t border-gray-200 px-3 py-3">
                            <div data-tool-field="headers_str" class="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary/30" tabindex="-1">
                                <ToolHeadersEditor
                                    v-model="toolForm.headers_str"
                                    :error="fieldErrors.headers_str"
                                />
                            </div>

                            <div data-tool-field="signing" class="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary/30" tabindex="-1">
                                <p v-if="fieldErrors.signing" class="mb-1 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700">{{ fieldErrors.signing }}</p>
                                <ToolSigningEditor
                                    v-model:enabled="toolForm.signing_enabled"
                                    v-model:key-id="toolForm.signing_key_id"
                                    v-model:secret="toolForm.signing_secret"
                                    :configured="!!toolForm.signing_secret_configured"
                                />
                            </div>
                        </div>
                    </details>

                    <div class="border-t border-gray-100 pt-4">
                        <ApiToolTester
                            :method="toolForm.method || 'GET'"
                            :url-template="toolForm.url_template || ''"
                            :headers-str="toolForm.headers_str || '{}'"
                            :schema-str="toolForm.parameter_schema_str || '{}'"
                            :signing-enabled="!!toolForm.signing_enabled"
                            :signing-algorithm="toolForm.signing_algorithm || 'hmac-sha256'"
                            :signing-key-id="toolForm.signing_key_id"
                            :signing-secret="toolForm.signing_secret || ''"
                            :tool-id="toolForm.id"
                        />
                    </div>

                    <div class="flex items-center">
                        <input id="is_active" type="checkbox" v-model="toolForm.is_active" class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded" />
                        <label for="is_active" class="ml-2 block text-sm text-gray-900">启用此工具</label>
                    </div>
                </div>
              
                <div class="sticky bottom-0 z-10 -mx-4 -mb-4 mt-6 flex flex-wrap justify-end gap-3 border-t border-gray-100 bg-white/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:-mb-6 sm:px-6">
                    <button
                        type="button"
                        :disabled="saving"
                        class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                        @click="requestCloseModal"
                    >取消</button>
                    <button
                        type="button"
                        :disabled="saving"
                        class="inline-flex items-center gap-2 px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-60"
                        @click="saveTool"
                    >
                        <span v-if="saving" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"></span>
                        {{ saving ? '保存中...' : '保存' }}
                    </button>
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
                    <GroupSelect
                      v-model="batchGroupName"
                      class="mt-1"
                      :options="groupFilterOptions.groups.map(([name, count]) => ({ name, count }))"
                      placeholder="选择已有分组，或直接输入新分组（留空 = 清除分组）"
                    />
                </div>
                <div class="flex justify-end space-x-3 pt-2">
                    <button @click="showBatchGroupModal = false" class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">取消</button>
                    <button @click="confirmBatchGroup" class="px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark">确定</button>
                </div>
            </div>
        </div>
      </Teleport>

      <Teleport to="body">
        <div v-if="showGroupManager" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
          <div class="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl">
            <div class="flex items-start justify-between gap-3">
              <div>
                <h3 class="text-lg font-bold text-gray-900">分组管理</h3>
                <p class="mt-1 text-xs leading-relaxed text-gray-500">重命名会更新该组全部工具；输入已有分组名会自动合并。</p>
              </div>
              <button type="button" class="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600" aria-label="关闭分组管理" @click="showGroupManager = false">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>

            <div v-if="groupFilterOptions.groups.length || groupFilterOptions.ungrouped" class="mt-4 space-y-4">
              <div>
                <label for="group-manager-source" class="block text-sm font-medium text-gray-700">选择分组</label>
                <select id="group-manager-source" v-model="groupManagerSource" :disabled="groupManagerSaving" class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary" @change="onGroupManagerSourceChange">
                  <option v-for="[name, count] in groupFilterOptions.groups" :key="name" :value="name">{{ name }} ({{ count }})</option>
                  <option v-if="groupFilterOptions.ungrouped" :value="UNGROUPED_FILTER">未分组 ({{ groupFilterOptions.ungrouped }})</option>
                </select>
                <p class="mt-1 text-xs text-gray-400">当前组共 {{ groupManagerTools.length }} 个工具</p>
              </div>

              <div v-if="groupManagerTools.length">
                <label for="group-manager-target" class="block text-sm font-medium text-gray-700">新名称</label>
                <GroupSelect
                  id="group-manager-target"
                  v-model="groupManagerTarget"
                  class="mt-1"
                  :options="groupFilterOptions.groups.map(([name, count]) => ({ name, count }))"
                  placeholder="输入新名称，或选择已有分组进行合并"
                />
                <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
                  <button type="button" :disabled="groupManagerSaving || groupManagerSource === UNGROUPED_FILTER" class="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50" @click="clearSelectedGroup">清空分组</button>
                  <button type="button" :disabled="groupManagerSaving" class="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50" @click="renameOrMergeGroup">
                    {{ groupManagerSaving ? '处理中...' : '重命名 / 合并' }}
                  </button>
                </div>
              </div>
            </div>
            <p v-else class="mt-4 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-4 text-center text-xs text-gray-400">还没有可管理的分组。</p>
          </div>
        </div>
      </Teleport>

      <ApiToolTester
        v-if="quickTestTool"
        :method="quickTestTool.method || 'GET'"
        :url-template="quickTestTool.url_template || ''"
        :headers-str="JSON.stringify(quickTestTool.headers || {})"
        :schema-str="JSON.stringify(quickTestTool.parameter_schema || {})"
        :signing-enabled="!!quickTestTool.signing_enabled"
        :signing-algorithm="quickTestTool.signing_algorithm || 'hmac-sha256'"
        :signing-key-id="quickTestTool.signing_key_id"
        :signing-secret="''"
        :tool-id="quickTestTool.id"
        :tool-label="quickTestTool.name"
        auto-open
        hide-trigger
        @closed="quickTestTool = null"
      />

      <ConfirmModal
          v-if="toggleTarget"
          :title="toggleTarget.active ? '启用工具' : '停用工具'"
          :message="toggleTarget.active ? `启用「${toggleTarget.tool.name}」后，智能体可以立即调用它。` : `停用「${toggleTarget.tool.name}」后，智能体将不再调用它，但配置会保留。`"
          :confirm-text="toggleTarget.active ? '确认启用' : '确认停用'"
          @confirm="confirmToggleActive"
          @cancel="toggleTarget = null"
      />

      <ConfirmModal
          v-if="showGroupClearConfirm"
          title="清空分组"
          :message="`确定要把「${groupManagerSource === UNGROUPED_FILTER ? '未分组' : groupManagerSource}」里的 ${groupManagerTools.length} 个工具移出分组吗？工具本身不会被删除。`"
          confirm-text="清空分组"
          @confirm="applyGroupChange(null)"
          @cancel="showGroupClearConfirm = false"
      />

      <ConfirmModal
          v-if="showDiscardConfirm"
          title="放弃未保存的修改？"
          message="关闭后，本次修改的名称、参数、Headers 和签名配置都会丢失。"
          confirm-text="放弃修改"
          @confirm="confirmDiscard"
          @cancel="showDiscardConfirm = false"
      />

      <ConfirmModal
          v-if="showDeleteConfirm"
          title="确认删除工具"
          :message="`确定要删除工具「${deletingTool?.name || ''}」吗？删除后无法恢复。`"
          confirm-text="确认删除"
          @confirm="confirmDeleteTool"
          @cancel="showDeleteConfirm = false; deletingTool = null"
      />
      <ToolBundleImportModal
          :show="showBundleImport"
          @close="showBundleImport = false"
          @imported="handleBundleImported"
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
