<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { modelApi, type AIModel, type AIModelCreate, type AIModelOption, type AIModelReference, type AIModelUpdate, type ReasoningEffort } from '../../api/model'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import ConfirmModal from '../ConfirmModal.vue'
import ModelUsageDrawer from './ModelUsageDrawer.vue'
import { 
  PlayIcon,
  PencilSquareIcon,
  TrashIcon,
  DocumentDuplicateIcon
} from '@heroicons/vue/24/outline'

const { showToast } = useToast()
const { hasPermission } = useUser()
const canSave = hasPermission('element:system:config_save')

const models = ref<AIModel[]>([])
const loadingModels = ref(false)
const modelSearchQuery = ref('')
const modelProviderFilter = ref('all')
const modelTypeFilter = ref('all')
const modelStatusFilter = ref('all')
const testingModelId = ref<string | null>(null)
const testingFormModel = ref(false)
const showModelModal = ref(false)
const isEditingModel = ref(false)
const showDeleteConfirm = ref(false)
const pendingDeleteModel = ref<AIModel | null>(null)
const showStatusConfirm = ref(false)
const pendingStatusModel = ref<AIModel | null>(null)
const pendingStatusValue = ref(false)
const pendingModelReferences = ref<AIModelReference[]>([])
const showModelUsage = ref(false)
const usageModel = ref<AIModel | null>(null)
const usageReferences = ref<AIModelReference[]>([])
const loadingModelUsage = ref(false)
const modelUsageError = ref('')
const showProviderMenu = ref(false)
const showModelPicker = ref(false)
const showAdvancedModelOptions = ref(false)
const loadingDiscoveredModels = ref(false)
const discoveredModels = ref<AIModelOption[]>([])
const reasoningEffortOptions: Array<{ value: ReasoningEffort; label: string; description: string }> = [
  { value: 'none', label: '无（none）', description: '普通问答、摘要、改写' },
  { value: 'minimal', label: '极简（minimal）', description: '简单判断、轻量分析' },
  { value: 'low', label: '低（low）', description: '常规代码、一般分析' },
  { value: 'medium', label: '中（medium）', description: '多步骤分析、复杂问答' },
  { value: 'high', label: '高（high）', description: 'Debug、SQL、复杂分析、Agent' },
  { value: 'xhigh', label: '极高（xhigh）', description: '极难 Coding Agent、长任务' },
]
const defaultSupportedReasoningEfforts: ReasoningEffort[] = reasoningEffortOptions.map((option) => option.value)
type ModelForm = Partial<AIModelCreate> & Pick<Partial<AIModel>, 'id' | 'has_api_key'>
const modelForm = ref<ModelForm>({
  name: '',
  model_id: '',
  provider: 'openai',
  type: 'llm',
  api_base_url: 'https://api.openai.com/v1',
  api_key: '',
  is_active: true,
  thinking_enable: false,
  thinking_only: false,
  allow_disable_thinking: true,
  reasoning_effort: null,
  supported_reasoning_efforts: [...defaultSupportedReasoningEfforts],
})

const modelIdConflict = computed(() => {
    const modelId = String(modelForm.value.model_id || '').trim()
    if (!modelId) return false
    return models.value.some((model) => model.model_id === modelId && model.id !== modelForm.value.id)
})

const providerDefaultBaseUrls: Record<string, string> = {
    openai: 'https://api.openai.com/v1',
    deepseek: 'https://api.deepseek.com',
    kimi: 'https://api.moonshot.cn/v1',
    zhipu: 'https://open.bigmodel.cn/api/paas/v4',
    siliconflow: 'https://api.siliconflow.cn/v1',
    dashscope: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    volcengine: 'https://ark.cn-beijing.volces.com/api/v3',
    ollama: 'http://localhost:11434/v1',
}
const providerLabels: Record<string, string> = {
    openai: 'OpenAI',
    azure: 'Azure OpenAI',
    deepseek: 'DeepSeek',
    kimi: 'Kimi（月之暗面）',
    zhipu: '智谱 AI (GLM)',
    siliconflow: '硅基流动',
    dashscope: '阿里云百炼 (DashScope)',
    volcengine: '火山引擎 (Ark/豆包)',
    ollama: 'Ollama (Local)',
    other: '其他 OpenAI 兼容服务',
}
const providerCatalog = [
    { value: 'openai', label: 'OpenAI', icon: 'AI', color: '#111827' },
    { value: 'azure', label: 'Azure OpenAI', icon: 'AZ', color: '#2563eb' },
    { value: 'deepseek', label: 'DeepSeek', icon: 'DS', color: '#2563eb' },
    { value: 'kimi', label: 'Kimi（月之暗面）', icon: 'K', color: '#7c3aed' },
    { value: 'zhipu', label: '智谱 AI (GLM)', icon: 'GLM', color: '#0f766e' },
    { value: 'siliconflow', label: '硅基流动', icon: 'SF', color: '#ea580c' },
    { value: 'dashscope', label: '阿里云百炼', icon: 'Q', color: '#0891b2' },
    { value: 'volcengine', label: '火山引擎 (Ark/豆包)', icon: 'ARK', color: '#f95738' },
    { value: 'ollama', label: 'Ollama (Local)', icon: 'OL', color: '#374151' },
    { value: 'other', label: '其他 OpenAI 兼容服务', icon: 'API', color: '#64748b' },
]
const supportedProviders = new Set(Object.keys(providerDefaultBaseUrls).concat(['azure', 'other']))
const supportedTypes = new Set(['llm', 'embedding', 'multimodal'])
const contextSizePresets = [32768, 65536, 131072, 262144]
const outputTokenPresets = [8192, 16384, 32768, 65536]
const lastProvider = ref<string>('openai')
const selectedProvider = computed(() =>
    providerCatalog.find((item) => item.value === String(modelForm.value.provider)) || providerCatalog[0]!
)
const providerMeta = (provider: string) =>
    providerCatalog.find((item) => item.value === provider) || providerCatalog[providerCatalog.length - 1]!
const knownProviderValues = new Set(providerCatalog.map((provider) => provider.value))

const filteredModels = computed(() => {
    const keyword = modelSearchQuery.value.trim().toLowerCase()
    return models.value.filter((model) => {
        const matchesKeyword = !keyword || [model.name, model.model_id]
            .some((value) => String(value || '').toLowerCase().includes(keyword))
        const providerKey = knownProviderValues.has(model.provider) ? model.provider : 'other'
        const matchesProvider = modelProviderFilter.value === 'all' || providerKey === modelProviderFilter.value
        const matchesType = modelTypeFilter.value === 'all' || model.type === modelTypeFilter.value
        const matchesStatus = modelStatusFilter.value === 'all'
            || (modelStatusFilter.value === 'active' && model.is_active)
            || (modelStatusFilter.value === 'inactive' && !model.is_active)
        return matchesKeyword && matchesProvider && matchesType && matchesStatus
    })
})

const hasModelFilters = computed(() => Boolean(
    modelSearchQuery.value.trim()
    || modelProviderFilter.value !== 'all'
    || modelTypeFilter.value !== 'all'
    || modelStatusFilter.value !== 'all'
))

const clearModelFilters = () => {
    modelSearchQuery.value = ''
    modelProviderFilter.value = 'all'
    modelTypeFilter.value = 'all'
    modelStatusFilter.value = 'all'
}

const formatTokenSize = (value?: number | null) => {
    if (!value) return ''
    if (value >= 1024 && value % 1024 === 0) return `${value / 1024}K`
    return String(value)
}

const normalizeThinkingConfiguration = (model: ModelForm): ModelForm => {
    const configuredEfforts = Array.isArray(model.supported_reasoning_efforts)
        ? model.supported_reasoning_efforts
        : defaultSupportedReasoningEfforts
    const supportedReasoningEfforts = reasoningEffortOptions
        .map((option) => option.value)
        .filter((effort) => configuredEfforts.includes(effort))
    const reasoningEffort = reasoningEffortOptions.some((option) => option.value === model.reasoning_effort)
        ? model.reasoning_effort
        : null
    return {
        ...model,
        thinking_enable: model.thinking_enable ?? false,
        thinking_only: model.thinking_only ?? false,
        allow_disable_thinking: model.allow_disable_thinking ?? true,
        reasoning_effort: reasoningEffort,
        supported_reasoning_efforts: supportedReasoningEfforts.length
            ? supportedReasoningEfforts
            : [...defaultSupportedReasoningEfforts],
    }
}

const isReasoningEffortSupported = (effort: ReasoningEffort) =>
    Boolean(modelForm.value.supported_reasoning_efforts?.includes(effort))

const handleReasoningEffortChange = (effort: ReasoningEffort, event: Event) => {
    const checked = (event.target as HTMLInputElement).checked
    const current = modelForm.value.supported_reasoning_efforts || []
    const next = checked
        ? [...new Set([...current, effort])]
        : current.filter((item) => item !== effort)
    if (!next.length) {
        showToast('至少保留一个支持的思考强度', 'warning')
        return
    }
    modelForm.value.supported_reasoning_efforts = reasoningEffortOptions
        .map((option) => option.value)
        .filter((item) => next.includes(item))
    if (
        modelForm.value.reasoning_effort !== null
        && !modelForm.value.supported_reasoning_efforts.includes(modelForm.value.reasoning_effort as ReasoningEffort)
    ) {
        modelForm.value.reasoning_effort = modelForm.value.supported_reasoning_efforts[0]
    }
}

const hasConfiguredThinking = computed(() => {
    const supported = modelForm.value.supported_reasoning_efforts || []
    const hasNonDefaultSupported = supported.length !== defaultSupportedReasoningEfforts.length
        || defaultSupportedReasoningEfforts.some((effort) => !supported.includes(effort))
    return Boolean(
        modelForm.value.thinking_enable
        || modelForm.value.thinking_only
        || modelForm.value.allow_disable_thinking === false
        || modelForm.value.reasoning_effort !== null
        || hasNonDefaultSupported
    )
})

const providerBaseUrlHint = computed(() => {
    const provider = String(modelForm.value.provider || '')
    if (provider === 'azure') return 'Azure 需要填写你的资源专属 Endpoint'
    if (provider === 'other') return '可填写代理或其他 OpenAI 兼容服务地址'
    return providerDefaultBaseUrls[provider]
        ? `已提供默认地址：${providerDefaultBaseUrls[provider]}，也可以手工覆盖`
        : '系统将根据供应商提供默认地址'
})

const canDiscoverModels = computed(() => {
    const provider = String(modelForm.value.provider || '')
    return Boolean(
        provider &&
        provider !== 'azure' &&
        String(modelForm.value.api_base_url || '').trim()
    )
})

const handleProviderChange = () => {
    const provider = String(modelForm.value.provider || '')
    const currentUrl = String(modelForm.value.api_base_url || '').trim()
    const previousDefault = providerDefaultBaseUrls[lastProvider.value]
    if (!currentUrl || (previousDefault && currentUrl === previousDefault)) {
        modelForm.value.api_base_url = providerDefaultBaseUrls[provider] || ''
    }
    lastProvider.value = provider
}

const fetchModels = async () => {
    loadingModels.value = true
    try {
        const res = await modelApi.list(undefined, true)
        models.value = res.data
    } catch (e: any) {
        showToast('获取模型列表失败', 'error')
    } finally {
        loadingModels.value = false
    }
}

const loadModelReferences = async (model: AIModel) => {
    try {
        const response = await modelApi.references(model.id)
        pendingModelReferences.value = response.data
    } catch (e: any) {
        pendingModelReferences.value = []
        showToast('读取模型引用关系失败，将继续显示确认提示', 'warning')
    }
}

const openModelUsage = async (model: AIModel) => {
    usageModel.value = model
    usageReferences.value = []
    modelUsageError.value = ''
    showModelUsage.value = true
    loadingModelUsage.value = true
    try {
        const response = await modelApi.references(model.id)
        if (usageModel.value?.id === model.id) {
            usageReferences.value = response.data
        }
    } catch (e: any) {
        modelUsageError.value = e.response?.data?.detail || e.message || '读取模型使用关系失败'
    } finally {
        if (usageModel.value?.id === model.id) {
            loadingModelUsage.value = false
        }
    }
}

const closeModelUsage = () => {
    showModelUsage.value = false
    usageModel.value = null
    usageReferences.value = []
    modelUsageError.value = ''
}

const requestStatusChange = async (model: AIModel) => {
    pendingStatusModel.value = model
    pendingStatusValue.value = !model.is_active
    pendingModelReferences.value = []
    if (model.is_active) {
        await loadModelReferences(model)
    }
    showStatusConfirm.value = true
}

const referenceWarning = (action: string) => {
    if (!pendingModelReferences.value.length) return ''
    return `\n\n注意：${action}后仍有 ${pendingModelReferences.value.length} 个配置引用此模型，运行时将无法调用。请先切换这些配置。`
}

const modelReferenceDetails = computed(() => pendingModelReferences.value.map((reference) =>
    `${reference.label}（${reference.detail}）`
))

const closeStatusConfirm = () => {
    showStatusConfirm.value = false
    pendingStatusModel.value = null
    pendingModelReferences.value = []
}

const confirmStatusChange = async () => {
    const model = pendingStatusModel.value
    if (!model) return
    try {
        await modelApi.update(model.id, { is_active: pendingStatusValue.value })
        showToast(pendingStatusValue.value ? '模型已启用' : '模型已禁用', 'success')
        closeStatusConfirm()
        await fetchModels()
    } catch (e: any) {
        showToast(`状态更新失败: ${e.response?.data?.message || e.response?.data?.detail || e.message}`, 'error')
    }
}

const discoverProviderModels = async () => {
    const provider = String(modelForm.value.provider || '')
    if (provider === 'azure') {
        showToast('Azure OpenAI 请手工填写部署名称', 'warning')
        return
    }
    loadingDiscoveredModels.value = true
    showModelPicker.value = false
    try {
        const response = await modelApi.discover({
            provider,
            api_base_url: modelForm.value.api_base_url,
            api_key: modelForm.value.api_key,
            model_config_id: modelForm.value.id,
        })
        discoveredModels.value = response.data
        showModelPicker.value = true
        if (!response.data.length) {
            showToast('供应商没有返回可用模型', 'warning')
        }
    } catch (e: any) {
        showToast(`加载模型列表失败: ${e.response?.data?.message || e.response?.data?.detail || e.message}`, 'error')
    } finally {
        loadingDiscoveredModels.value = false
    }
}

const selectDiscoveredModel = (option: AIModelOption) => {
    modelForm.value.model_id = option.model_id
    if (!String(modelForm.value.name || '').trim()) {
        modelForm.value.name = option.name || option.model_id
    }
    showModelPicker.value = false
}

const testModel = async (model: AIModel) => {
    testingModelId.value = model.id
    try {
        const res = await modelApi.testConnection(model.id)
        if (res.data.status === 'success') {
            showToast(`${model.name}: ${res.data.message}`, 'success')
        } else {
            showToast(`${model.name}: ${res.data.message}`, 'error')
        }
    } catch (e: any) {
        showToast(`请求失败: ${e.response?.data?.detail || e.message}`, 'error')
    } finally {
        testingModelId.value = null
    }
}

const normalizeOptionalInt = (val: any): number | null => {
    if (val === null || val === undefined || val === '' || (typeof val === 'number' && isNaN(val))) {
        return null
    }
    const parsed = parseInt(String(val), 10)
    return isNaN(parsed) || parsed <= 0 ? null : parsed
}

const testCurrentModel = async () => {
    const modelId = String(modelForm.value.model_id || '').trim()
    const modelType = String(modelForm.value.type || '')
    if (!modelId) {
        showToast('请先填写模型 ID', 'warning')
        return
    }
    testingFormModel.value = true
    try {
        const response = await modelApi.testConfig({
            provider: String(modelForm.value.provider || 'openai'),
            type: modelType,
            model_id: modelId,
            api_base_url: modelForm.value.api_base_url,
            api_key: modelForm.value.api_key,
            context_size: normalizeOptionalInt(modelForm.value.context_size),
            max_output_tokens: normalizeOptionalInt(modelForm.value.max_output_tokens),
            model_config_id: modelForm.value.id,
        })
        if (response.data.status === 'success') {
            showToast(`连接成功${response.data.response ? `：${response.data.response}` : ''}`, 'success')
        } else {
            showToast(response.data.message || '连接失败', 'error')
        }
    } catch (e: any) {
        showToast(`测试连接失败: ${e.response?.data?.detail || e.message}`, 'error')
    } finally {
        testingFormModel.value = false
    }
}

const openModelModal = (model?: AIModel, isClone = false) => {
    showProviderMenu.value = false
    showModelPicker.value = false
    showAdvancedModelOptions.value = false
    discoveredModels.value = []
    if (model) {
        if (isClone) {
            isEditingModel.value = false
            modelForm.value = normalizeThinkingConfiguration({
                ...model, 
                id: undefined, 
                name: `${model.name} (Copy)`,
                model_id: `${model.model_id}-copy`,
                api_key: '' 
            })
        } else {
            isEditingModel.value = true
            modelForm.value = normalizeThinkingConfiguration({ ...model, api_key: '' })
        }
    } else {
        isEditingModel.value = false
        modelForm.value = normalizeThinkingConfiguration({
            name: '',
            model_id: '',
            provider: 'openai',
            type: 'llm',
            api_base_url: providerDefaultBaseUrls.openai,
            api_key: '',
            is_active: true,
        })
    }
    lastProvider.value = String(modelForm.value.provider || 'openai')
    showModelModal.value = true
}

const cloneModel = (model: AIModel) => {
    openModelModal(model, true)
}

const saveModel = async () => {
    if (!modelForm.value.name || !modelForm.value.model_id) {
        showToast('请填写名称和模型ID', 'warning')
        return
    }
    if (modelIdConflict.value) {
        showToast('模型 ID 已存在，请更换后再保存', 'warning')
        return
    }
    
    try {
        const payload: AIModelUpdate = {
            name: modelForm.value.name,
            model_id: modelForm.value.model_id,
            api_base_url: modelForm.value.api_base_url,
            context_size: normalizeOptionalInt(modelForm.value.context_size),
            max_output_tokens: normalizeOptionalInt(modelForm.value.max_output_tokens),
            thinking_enable: modelForm.value.thinking_enable,
            thinking_only: modelForm.value.thinking_only,
            allow_disable_thinking: modelForm.value.allow_disable_thinking,
            reasoning_effort: modelForm.value.reasoning_effort,
            supported_reasoning_efforts: modelForm.value.supported_reasoning_efforts,
            api_key: modelForm.value.api_key,
            is_active: modelForm.value.is_active,
        }
        // Legacy rows may contain provider/type values that are no longer
        // supported. Omit unchanged legacy values so a harmless name edit can
        // still be saved; selecting a supported value submits it normally.
        if (supportedProviders.has(String(modelForm.value.provider))) {
            payload.provider = modelForm.value.provider
        }
        if (supportedTypes.has(String(modelForm.value.type))) {
            payload.type = modelForm.value.type
        }
        if (payload.api_key === '') {
            delete payload.api_key
        }

        if (isEditingModel.value && modelForm.value.id) {
            await modelApi.update(modelForm.value.id, payload)
            showToast('更新成功', 'success')
        } else {
            await modelApi.create(payload as AIModelCreate)
            showToast('创建成功', 'success')
        }
        showModelModal.value = false
        fetchModels()
    } catch (e: any) {
        showToast('保存失败: ' + (e.response?.data?.message || e.response?.data?.detail || e.message), 'error')
    }
}

const deleteModel = async (model: AIModel) => {
    pendingDeleteModel.value = model
    await loadModelReferences(model)
    showDeleteConfirm.value = true
}

const closeDeleteConfirm = () => {
    showDeleteConfirm.value = false
    pendingDeleteModel.value = null
    pendingModelReferences.value = []
}

const confirmDeleteModel = async () => {
    const model = pendingDeleteModel.value
    if (!model) return
    try {
        await modelApi.delete(model.id)
        showToast('已删除', 'success')
        closeDeleteConfirm()
        fetchModels()
    } catch(e: any) {
        showToast('删除失败', 'error')
    }
}

const closeFloatingMenus = () => {
    showProviderMenu.value = false
    showModelPicker.value = false
}

// Expose refresh to parent if needed
defineExpose({ refresh: fetchModels })

onMounted(() => {
  fetchModels()
  document.addEventListener('click', closeFloatingMenus)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', closeFloatingMenus)
})
</script>

<template>
  <div class="registry-scroll h-full min-h-0 overflow-y-auto pb-6 p-1">
      <div class="bg-white shadow rounded-lg overflow-hidden">
         <div class="p-4 border-b border-gray-100 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h3 class="text-lg font-medium text-gray-900">AI 模型注册表</h3>
            <div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
                <input
                    v-model="modelSearchQuery"
                    type="search"
                    placeholder="搜索模型名称或 ID..."
                    class="w-full sm:w-52 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <select v-model="modelProviderFilter" class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按供应商筛选">
                    <option value="all">供应商：全部</option>
                    <option v-for="provider in providerCatalog" :key="provider.value" :value="provider.value">{{ provider.label }}</option>
                </select>
                <select v-model="modelTypeFilter" class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按模型类型筛选">
                    <option value="all">类型：全部</option>
                    <option value="llm">LLM</option>
                    <option value="embedding">Embedding</option>
                    <option value="multimodal">Multimodal</option>
                </select>
                <select v-model="modelStatusFilter" class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按状态筛选">
                    <option value="all">状态：全部</option>
                    <option value="active">启用</option>
                    <option value="inactive">停用</option>
                </select>
                <button
                    type="button"
                    class="px-2.5 py-2 text-sm text-gray-500 hover:text-primary"
                    :class="{ invisible: !hasModelFilters }"
                    :disabled="!hasModelFilters"
                    :tabindex="hasModelFilters ? 0 : -1"
                    @click="clearModelFilters"
                >清空</button>
                <button
                    v-if="canSave"
                    @click="openModelModal()"
                    class="px-3 py-2 bg-primary text-white text-sm rounded-md hover:bg-primary-dark transition-colors"
                >
                    + 添加模型
                </button>
            </div>
         </div>
         
         <div v-if="loadingModels && models.length === 0" class="p-8 text-center text-gray-400">加载中...</div>
         <div v-else class="relative overflow-x-auto">
         <div
            v-if="loadingModels"
            class="pointer-events-none absolute inset-0 z-10 bg-white/45"
            aria-busy="true"
            aria-label="正在刷新模型列表"
         ></div>
         <table class="min-w-[1080px] w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider min-w-[360px]">模型</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider min-w-[220px]">提供商</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">类型</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">状态</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider min-w-[150px]">使用关系</th>
                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider sticky right-0 bg-gray-50">操作</th>
                </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
                <tr v-for="m in filteredModels" :key="m.id" class="group hover:bg-gray-50">
                    <td class="px-6 py-4 min-w-[360px]">
                        <div class="model-cell">
                        <div class="text-sm font-semibold text-gray-900">{{ m.name }}</div>
                        <div class="mt-1 text-xs text-gray-500 font-mono truncate max-w-[520px]" :title="m.model_id">{{ m.model_id }}</div>
                        <div class="mt-2 flex min-h-[1.5rem] flex-wrap gap-1.5 text-[11px]">
                            <span v-if="m.context_size" class="token-limit-badge">输入 {{ formatTokenSize(m.context_size) }}</span>
                            <span v-if="m.max_output_tokens" class="token-limit-badge">输出 {{ formatTokenSize(m.max_output_tokens) }}</span>
                        </div>
                        </div>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <span class="inline-flex items-center gap-2 px-2.5 py-1 text-xs font-semibold rounded-full bg-blue-50 text-blue-800">
                            <span class="provider-mini-icon" :style="{ backgroundColor: providerMeta(m.provider).color }">{{ providerMeta(m.provider).icon }}</span>
                            {{ providerLabels[m.provider] || m.provider }}
                        </span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <span class="inline-flex items-center space-x-1.5 select-none">
                            <svg v-if="m.type === 'multimodal'" class="w-4 h-4 text-purple-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="多模态模型">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                            </svg>
                            <svg v-else class="w-4 h-4 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="语言模型">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                            </svg>
                            <span class="font-medium">{{ m.type === 'llm' ? 'LLM' : (m.type === 'multimodal' ? 'Multimodal' : m.type) }}</span>
                        </span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap">
                        <button
                            v-if="canSave"
                            type="button"
                            role="switch"
                            :aria-checked="m.is_active"
                            :title="m.is_active ? '禁用模型' : '启用模型'"
                            class="inline-flex items-center gap-2"
                            @click="requestStatusChange(m)"
                        >
                            <span class="status-switch" :class="m.is_active ? 'status-switch-on' : 'status-switch-off'">
                                <span class="status-switch-knob"></span>
                            </span>
                            <span class="text-xs font-semibold" :class="m.is_active ? 'text-green-700' : 'text-gray-500'">{{ m.is_active ? '启用' : '停用' }}</span>
                        </button>
                        <span v-else class="text-xs font-semibold" :class="m.is_active ? 'text-green-700' : 'text-gray-500'">{{ m.is_active ? '启用' : '停用' }}</span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap">
                        <button
                            v-if="canSave"
                            type="button"
                            class="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-50"
                            title="查看模型使用关系"
                            @click="openModelUsage(m)"
                        >
                            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 7h10M7 12h10M7 17h6M5 3h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z" />
                            </svg>
                            查看关系
                        </button>
                        <span v-else class="text-xs text-gray-400">—</span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium sticky right-0 bg-white group-hover:bg-gray-50 shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.4)]">
                        <div v-if="canSave" class="flex items-center justify-end space-x-2">
                            <button 
                                @click="testModel(m)" 
                                :disabled="testingModelId === m.id"
                                title="测试连接"
                                class="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded-md transition-colors disabled:opacity-50"
                            >
                                <svg v-if="testingModelId === m.id" class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                <PlayIcon v-else class="h-4 w-4" />
                            </button>
                            
                            <button 
                                @click="openModelModal(m)" 
                                title="编辑模型"
                                class="p-1.5 text-primary hover:bg-blue-50 rounded-md transition-colors"
                            >
                                <PencilSquareIcon class="h-4 w-4" />
                            </button>

                            <button 
                                @click="cloneModel(m)" 
                                title="复制模型"
                                class="p-1.5 text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
                            >
                                <DocumentDuplicateIcon class="h-4 w-4" />
                            </button>
                            
                            <button 
                                @click="deleteModel(m)" 
                                title="删除模型"
                                class="p-1.5 text-red-500 hover:bg-red-50 rounded-md transition-colors"
                            >
                                <TrashIcon class="h-4 w-4" />
                            </button>
                        </div>
                        <span v-else class="text-gray-400 italic text-xs">仅限管理</span>
                     </td>
                </tr>
                <tr v-if="filteredModels.length === 0">
                    <td colspan="6" class="px-6 py-8 text-center text-gray-400 text-sm">{{ hasModelFilters ? '暂无匹配模型' : '暂无模型配置' }}</td>
                </tr>
            </tbody>
         </table>
         </div>
      </div>

      <!-- Model Modal (Moved inside component for self-containment) -->
      <div v-if="showModelModal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm" @click="showProviderMenu = false; showModelPicker = false">
          <div class="bg-white rounded-xl shadow-xl max-w-xl w-full max-h-[90vh] overflow-y-auto p-6 space-y-4 text-left" @click.stop>
              <h3 class="text-lg font-bold text-gray-900">{{ isEditingModel ? '编辑模型' : '添加新模型' }}</h3>
              
              <div class="space-y-3">
                  <div>
                     <label class="block text-sm font-medium text-gray-700">提供商</label>
                     <div class="relative mt-1">
                         <button type="button" class="provider-select-trigger model-form-control" @click.stop="showProviderMenu = !showProviderMenu; showModelPicker = false">
                             <span class="flex items-center gap-2 min-w-0">
                                 <span class="provider-icon" :style="{ backgroundColor: selectedProvider.color }">{{ selectedProvider.icon }}</span>
                                 <span class="truncate">{{ selectedProvider.label }}</span>
                             </span>
                             <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" /></svg>
                         </button>
                         <div v-if="showProviderMenu" class="provider-menu" @click.stop>
                             <button
                                 v-for="provider in providerCatalog"
                                 :key="provider.value"
                                 type="button"
                                 class="provider-menu-item"
                                 :class="String(modelForm.provider) === provider.value ? 'provider-menu-item-active' : ''"
                                 @click="modelForm.provider = provider.value; handleProviderChange(); showProviderMenu = false"
                             >
                                 <span class="provider-icon" :style="{ backgroundColor: provider.color }">{{ provider.icon }}</span>
                                 <span class="text-left min-w-0">
                                     <span class="block truncate font-medium">{{ provider.label }}</span>
                                     <span class="block truncate text-[11px] text-gray-400">{{ providerDefaultBaseUrls[provider.value] || '需要手工填写接口地址' }}</span>
                                 </span>
                                 <svg v-if="String(modelForm.provider) === provider.value" class="w-4 h-4 ml-auto text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>
                             </button>
                         </div>
                     </div>
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">API Base URL</label>
                     <input v-model="modelForm.api_base_url" class="model-form-control mt-1" :placeholder="providerBaseUrlHint" />
                     <p class="provider-url-hint text-xs text-gray-500 mt-1">{{ providerBaseUrlHint }}</p>
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">API Key</label>
                     <input v-model="modelForm.api_key" type="password" class="model-form-control mt-1" :placeholder="isEditingModel && modelForm.has_api_key ? '已配置，留空则保留原密钥' : '留空则使用系统默认密钥'" />
                  </div>
                  <div>
                     <div class="flex items-center justify-between">
                         <label class="block text-sm font-medium text-gray-700">模型 ID (API)</label>
                         <button type="button" class="discover-model-button" :class="{ 'discover-model-button-disabled': !canDiscoverModels }" :disabled="loadingDiscoveredModels || !canDiscoverModels" :title="canDiscoverModels ? '加载当前供应商模型列表' : (String(modelForm.provider) === 'azure' ? 'Azure OpenAI 请手工填写部署名称' : '请先填写 API Base URL')" @click="discoverProviderModels">
                             <svg v-if="loadingDiscoveredModels" class="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/></svg>
                             <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M4 12h16M4 17h16" /></svg>
                             {{ loadingDiscoveredModels ? '加载中' : '加载模型列表' }}
                         </button>
                     </div>
                     <div class="relative mt-1">
                         <input v-model="modelForm.model_id" :class="{ 'model-form-control-invalid': modelIdConflict }" class="model-form-control font-mono pr-3" placeholder="例如: gpt-4o" />
                         <div v-if="showModelPicker" class="model-picker-menu" @click.stop>
                             <div class="flex items-center justify-between gap-3 px-3 py-2 border-b border-gray-100 text-xs text-gray-500">
                                 <span>选择 {{ providerLabels[String(modelForm.provider)] || modelForm.provider }} 模型</span>
                                 <button type="button" class="model-picker-close" aria-label="关闭模型列表" title="关闭" @click.stop="showModelPicker = false">×</button>
                             </div>
                             <button v-for="option in discoveredModels" :key="option.model_id" type="button" class="model-picker-item" @click="selectDiscoveredModel(option)">
                                 <span class="font-medium text-gray-800">{{ option.name }}</span>
                                 <span class="text-xs font-mono text-gray-500">{{ option.model_id }}</span>
                             </button>
                         </div>
                     </div>
                     <p class="text-xs text-gray-500 mt-1">云服务商定义的实际模型标识符；可手工填写，也可从供应商列表选择</p>
                     <p class="text-xs mt-1 min-h-[1rem]" :class="modelIdConflict ? 'text-red-600' : 'invisible'" aria-live="polite">该 model_id 已存在，模型 ID 必须全局唯一</p>
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">模型类型</label>
                     <select v-model="modelForm.type" class="model-form-control mt-1">
                         <option value="llm">LLM (文本生成)</option>
                         <option value="embedding">Embedding (向量)</option>
                         <option value="multimodal">Multimodal (多模态)</option>
                     </select>
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">模型名称</label>
                     <input v-model="modelForm.name" class="model-form-control mt-1" placeholder="例如: GPT-4o 生产版" />
                     <p class="text-xs text-gray-500 mt-1">用于系统界面展示，不影响实际 API 调用</p>
                  </div>
                  <template v-if="modelForm.type !== 'embedding'">
                      <button type="button" class="advanced-options-toggle" :aria-expanded="showAdvancedModelOptions" @click="showAdvancedModelOptions = !showAdvancedModelOptions">
                          <span class="flex items-center gap-2">
                              <svg class="w-4 h-4 transition-transform" :class="showAdvancedModelOptions ? 'rotate-90' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" /></svg>
                              <span>高级设置</span>
                          </span>
                          <span class="text-xs text-gray-400" :class="{ invisible: !(modelForm.context_size || modelForm.max_output_tokens || hasConfiguredThinking) }">已配置</span>
                      </button>
                      <div
                          class="advanced-options-panel"
                          :class="{ 'advanced-options-panel-open': showAdvancedModelOptions }"
                          :inert="!showAdvancedModelOptions"
                          :aria-hidden="!showAdvancedModelOptions"
                      >
                          <div class="advanced-options-panel-inner">
                              <section class="thinking-mode-section">
                                  <div class="advanced-section-heading">
                                      <div>
                                          <h4 class="advanced-section-title">思考能力与默认设置</h4>
                                          <p class="advanced-section-description">配置该模型是否支持思考控制，以及新会话的默认行为。</p>
                                      </div>
                                      <label class="thinking-mode-capsule thinking-mode-capsule-primary" :class="{ 'thinking-mode-capsule-on': modelForm.thinking_enable }">
                                          <input v-model="modelForm.thinking_enable" type="checkbox" class="sr-only" />
                                          <span class="thinking-mode-capsule-label">支持思考模式</span>
                                          <span class="thinking-mode-capsule-track">
                                              <span class="thinking-mode-capsule-thumb"></span>
                                              <span>{{ modelForm.thinking_enable ? '开启' : '关闭' }}</span>
                                          </span>
                                      </label>
                                  </div>
                                  <div class="thinking-provider-tip" role="note">
                                      <span class="thinking-provider-tip-label">配置建议</span>
                                      <span>开启“支持思考模式”后，平台会向供应商显式传递思考开关。若供应商默认开启思考，请保持此项开启，再关闭“新会话默认开启思考”。</span>
                                  </div>
                                  <div v-if="modelForm.thinking_enable">
                                      <div class="thinking-options-grid">
                                          <div class="thinking-option-card">
                                              <span>
                                                  <span class="block text-sm font-medium text-gray-700">{{ modelForm.thinking_only ? '新会话默认开启思考' : '新会话默认关闭思考' }}</span>
                                                  <span class="mt-1 block text-xs text-gray-500">{{ modelForm.thinking_only ? '新会话会默认进入思考模式；用户是否可以关闭，由右侧设置决定。' : '新会话会默认使用非思考模式；需要时，用户仍可在会话中手动开启。' }}</span>
                                              </span>
                                              <label class="thinking-mode-capsule" :class="{ 'thinking-mode-capsule-on': modelForm.thinking_only }">
                                                  <input v-model="modelForm.thinking_only" type="checkbox" class="sr-only" />
                                                  <span class="thinking-mode-capsule-track">
                                                      <span class="thinking-mode-capsule-thumb"></span>
                                                      <span>{{ modelForm.thinking_only ? '开启' : '关闭' }}</span>
                                                  </span>
                                              </label>
                                          </div>
                                          <div class="thinking-option-card">
                                              <span>
                                                  <span class="block text-sm font-medium text-gray-700">{{ modelForm.allow_disable_thinking ? '允许用户关闭思考' : '禁止用户关闭思考' }}</span>
                                                  <span class="mt-1 block text-xs text-gray-500">{{ modelForm.allow_disable_thinking ? '用户可以在当前会话中关闭思考；默认开启时仍可手动切换。' : '开启后，用户无法在本次会话中再关闭思考；默认关闭时仍可按需开启。' }}</span>
                                              </span>
                                              <label class="thinking-mode-capsule" :class="{ 'thinking-mode-capsule-on': modelForm.allow_disable_thinking }">
                                                  <input v-model="modelForm.allow_disable_thinking" type="checkbox" class="sr-only" />
                                                  <span class="thinking-mode-capsule-track">
                                                      <span class="thinking-mode-capsule-thumb"></span>
                                                      <span>{{ modelForm.allow_disable_thinking ? '开启' : '关闭' }}</span>
                                                  </span>
                                              </label>
                                          </div>
                                      </div>
                                      <div class="default-reasoning-effort-row">
                                          <div class="default-reasoning-effort-field">
                                              <label class="block text-sm font-medium text-gray-700">默认思考强度</label>
                                              <select v-model="modelForm.reasoning_effort" class="default-reasoning-effort-select mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                                                  <option :value="null">自动（使用请求层默认值）</option>
                                                  <option v-for="option in reasoningEffortOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                                              </select>
                                              <p class="mt-1 text-xs text-gray-500">选择“自动”仅表示不指定思考强度，不代表关闭思考。</p>
                                          </div>
                                      </div>
                                      <div class="supported-reasoning-section">
                                          <span class="block text-sm font-medium text-gray-700">支持的思考强度</span>
                                          <div class="thinking-effort-options">
                                              <label v-for="option in reasoningEffortOptions" :key="option.value" class="thinking-effort-option" :class="{ 'thinking-effort-option-selected': isReasoningEffortSupported(option.value) }">
                                                  <input
                                                      type="checkbox"
                                                      class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded"
                                                      :checked="isReasoningEffortSupported(option.value)"
                                                      @change="handleReasoningEffortChange(option.value, $event)"
                                                  />
                                                  <span class="thinking-effort-label">
                                                      <span>{{ option.label }}</span>
                                                      <span class="thinking-effort-description">{{ option.description }}</span>
                                                  </span>
                                              </label>
                                          </div>
                                          <p class="mt-1 text-xs text-gray-500">至少保留一个强度；默认值为自动时不要求勾选自动。</p>
                                      </div>
                                  </div>
                              </section>
                              <section class="advanced-context-section">
                                  <div class="advanced-section-heading">
                                      <div>
                                          <h4 class="advanced-section-title">上下文与输出</h4>
                                          <p class="advanced-section-description">配置上下文窗口和单次请求的输出上限，留空使用供应商默认值。</p>
                                      </div>
                                  </div>
                                  <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                      <div>
                                          <label class="block text-sm font-medium text-gray-700">输入上下文（可选）</label>
                                          <input v-model.number="modelForm.context_size" type="number" min="1" step="1" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="使用供应商默认值" />
                                          <div class="token-preset-row">
                                              <button v-for="size in contextSizePresets" :key="size" type="button" class="token-preset-button" :class="modelForm.context_size === size ? 'token-preset-button-active' : ''" @click="modelForm.context_size = size">{{ formatTokenSize(size) }}</button>
                                          </div>
                                          <p class="text-xs text-gray-500 mt-1">用于上下文压缩；留空使用运行时默认值</p>
                                      </div>
                                      <div>
                                          <label class="block text-sm font-medium text-gray-700">输出上限（可选）</label>
                                          <input v-model.number="modelForm.max_output_tokens" type="number" min="1" step="1" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="使用供应商默认值" />
                                          <div class="token-preset-row">
                                              <button v-for="size in outputTokenPresets" :key="size" type="button" class="token-preset-button" :class="modelForm.max_output_tokens === size ? 'token-preset-button-active' : ''" @click="modelForm.max_output_tokens = size">{{ formatTokenSize(size) }}</button>
                                          </div>
                                          <p class="text-xs text-gray-500 mt-1">发送为 API 的最大输出 token；留空使用供应商默认值</p>
                                      </div>
                                  </div>
                              </section>
                          </div>
                      </div>
                  </template>
                  <div class="flex items-center">
                      <input id="is_active" type="checkbox" v-model="modelForm.is_active" class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded" />
                      <label for="is_active" class="ml-2 block text-sm text-gray-900">启用此模型</label>
                  </div>
              </div>
              
              <div class="flex justify-end space-x-3 mt-6">
                  <button @click="showModelModal = false" class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">取消</button>
                  <button type="button" @click="testCurrentModel" :disabled="testingFormModel || !String(modelForm.model_id || '').trim()" class="inline-flex items-center gap-2 px-4 py-2 border border-blue-200 rounded-md text-sm font-medium text-primary bg-blue-50 hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed">
                      <svg v-if="testingFormModel" class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path></svg>
                      {{ testingFormModel ? '测试中' : '测试连接' }}
                  </button>
                  <button @click="saveModel" :disabled="modelIdConflict" class="px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark disabled:opacity-50 disabled:cursor-not-allowed">保存</button>
              </div>
          </div>
      </div>

      <ConfirmModal
        v-if="showStatusConfirm && pendingStatusModel"
        :title="pendingStatusValue ? '启用模型' : '禁用模型'"
        :message="pendingStatusValue ? `确定启用模型「${pendingStatusModel.name}」吗？启用后它会重新出现在模型选择列表中。` : `确定禁用模型「${pendingStatusModel.name}」吗？禁用后新请求将不能选择它。${referenceWarning('禁用')}`"
        :details="!pendingStatusValue ? modelReferenceDetails : []"
        details-label="受影响配置"
        :confirm-text="pendingStatusValue ? '确认启用' : '确认禁用'"
        cancel-text="取消"
        :type="pendingStatusValue ? 'primary' : 'danger'"
        @confirm="confirmStatusChange"
        @cancel="closeStatusConfirm"
      />

      <ConfirmModal
        v-if="showDeleteConfirm && pendingDeleteModel"
        title="删除模型"
        :message="`确定要删除模型「${pendingDeleteModel.name}」吗？删除后将无法恢复。${referenceWarning('删除')}`"
        :details="modelReferenceDetails"
        details-label="受影响配置"
        confirm-text="删除"
        cancel-text="取消"
        type="danger"
        @confirm="confirmDeleteModel"
        @cancel="closeDeleteConfirm"
      />

      <ModelUsageDrawer
        :open="showModelUsage"
        :model="usageModel"
        :references="usageReferences"
        :loading="loadingModelUsage"
        :error="modelUsageError"
        @close="closeModelUsage"
      />
  </div>
</template>

<style scoped>
.registry-scroll {
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: rgba(156, 163, 175, 0.3) transparent;
}

.registry-scroll::-webkit-scrollbar {
  width: 6px;
}
.registry-scroll::-webkit-scrollbar-track {
  background: transparent;
}
.registry-scroll::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 3px;
}
.registry-scroll::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.5);
}

.model-cell {
  min-height: 4.25rem;
}

.provider-url-hint {
  min-height: 2.5rem;
}

.advanced-options-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 180ms ease;
}

.advanced-options-panel-open {
  grid-template-rows: 1fr;
}

.advanced-options-panel-inner {
  min-height: 0;
  overflow: hidden;
  padding-top: 0.25rem;
}

.provider-icon,
.provider-mini-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: white;
  font-weight: 700;
  letter-spacing: -0.04em;
}

.provider-icon {
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.55rem;
  font-size: 0.65rem;
}

.provider-mini-icon {
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 0.4rem;
  font-size: 0.5rem;
}

.model-form-control {
  display: block;
  width: 100%;
  min-height: 2.75rem;
  border: 1.5px solid rgb(203 213 225);
  border-radius: 0.625rem;
  background: rgb(248 250 252);
  padding: 0.65rem 0.8rem;
  color: rgb(30 41 59);
  outline: none;
  transition: border-color 150ms, background-color 150ms, box-shadow 150ms;
}

.model-form-control::placeholder {
  color: rgb(148 163 184);
}

.model-form-control:focus {
  border-color: rgb(96 165 250);
  background: white;
  box-shadow: 0 0 0 3px rgb(219 234 254), 0 1px 2px rgba(15, 23, 42, 0.08);
}

.model-form-control-invalid,
.model-form-control-invalid:focus {
  border-color: rgb(248 113 113);
  box-shadow: 0 0 0 3px rgb(254 226 226);
}

.provider-select-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  font-size: 0.875rem;
  color: rgb(31 41 55);
}

.provider-select-trigger:hover {
  border-color: rgb(148 163 184);
  background: white;
}

.provider-menu,
.model-picker-menu {
  position: absolute;
  z-index: 60;
  overflow: hidden;
  border: 1px solid rgb(226 232 240);
  border-radius: 0.75rem;
  background: white;
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.16);
}

.provider-menu {
  left: 0;
  right: 0;
  top: calc(100% + 0.35rem);
  max-height: 18rem;
  overflow-y: auto;
  padding: 0.3rem;
}

.provider-menu-item,
.model-picker-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.65rem;
  border-radius: 0.55rem;
  padding: 0.55rem 0.6rem;
  text-align: left;
  transition: background-color 150ms;
}

.token-limit-badge {
  display: inline-flex;
  align-items: center;
  border-radius: 9999px;
  background: rgb(248 250 252);
  color: rgb(100 116 139);
  padding: 0.15rem 0.45rem;
}

.advanced-options-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid rgb(226 232 240);
  padding-top: 0.75rem;
  color: rgb(51 65 85);
  font-size: 0.875rem;
  font-weight: 600;
  text-align: left;
}

.advanced-options-toggle:hover {
  color: rgb(37 99 235);
}

.token-preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.4rem;
}

.token-preset-button {
  border: 1px solid transparent;
  border-radius: 9999px;
  padding: 0.15rem 0.45rem;
  color: rgb(51 65 85);
  font-size: 0.75rem;
}

.token-preset-button:hover,
.token-preset-button-active {
  border-color: rgb(203 213 225);
  background: white;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.thinking-mode-section {
  padding-bottom: 1rem;
}

.advanced-context-section {
  border-top: 1px solid rgb(226 232 240);
  padding-top: 1rem;
}

.advanced-section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.advanced-section-title {
  color: rgb(51 65 85);
  font-size: 1rem;
  font-weight: 700;
}

.advanced-section-description {
  margin-top: 0.25rem;
  color: rgb(100 116 139);
  font-size: 0.8125rem;
}

.thinking-mode-capsule {
  display: inline-flex;
  cursor: pointer;
  align-items: center;
}

.thinking-mode-capsule-primary {
  flex-shrink: 0;
  gap: 0.5rem;
  white-space: nowrap;
}

.thinking-mode-capsule-label {
  white-space: nowrap;
}

.thinking-mode-capsule-primary .thinking-mode-capsule-track {
  min-width: 3.85rem;
}

.thinking-mode-capsule-track {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 4.5rem;
  min-height: 1.625rem;
  border-radius: 9999px;
  background: rgb(226 232 240);
  padding: 0.15rem 0.4rem 0.15rem 1.75rem;
  color: rgb(71 85 105);
  font-size: 0.6875rem;
  font-weight: 600;
  line-height: 1;
  transition: background-color 150ms, color 150ms;
}

.thinking-mode-capsule-thumb {
  position: absolute;
  left: 0.1875rem;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 9999px;
  background: white;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.2);
  transition: left 150ms;
}

.thinking-mode-capsule-on .thinking-mode-capsule-track {
  background: rgb(22 163 74);
  color: white;
  justify-content: flex-start;
  padding-right: 1.75rem;
  padding-left: 0.4rem;
}

.thinking-mode-capsule-on .thinking-mode-capsule-thumb {
  left: calc(100% - 1.4375rem);
}

.thinking-provider-tip {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-top: 0.75rem;
  border: 1px solid rgb(191 219 254);
  border-radius: 0.75rem;
  background: rgb(239 246 255);
  padding: 0.65rem 0.75rem;
  color: rgb(30 64 175);
  font-size: 0.75rem;
  line-height: 1.45;
}

.thinking-provider-tip-label {
  flex-shrink: 0;
  border-radius: 9999px;
  background: rgb(219 234 254);
  padding: 0.15rem 0.45rem;
  color: rgb(30 64 175);
  font-size: 0.6875rem;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
}

.thinking-option-card .thinking-mode-capsule-track {
  min-width: 3.85rem;
  min-height: 1.375rem;
  padding: 0.125rem 0.32rem 0.125rem 1.45rem;
  font-size: 0.625rem;
}

.thinking-option-card .thinking-mode-capsule-thumb {
  left: 0.15rem;
  width: 1.05rem;
  height: 1.05rem;
}

.thinking-option-card .thinking-mode-capsule-on .thinking-mode-capsule-track {
  padding-right: 1.45rem;
  padding-left: 0.32rem;
}

.thinking-option-card .thinking-mode-capsule-on .thinking-mode-capsule-thumb {
  left: calc(100% - 1.2rem);
}

.thinking-options-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.default-reasoning-effort-row,
.supported-reasoning-section {
  border-top: 1px solid rgb(226 232 240);
  margin-top: 1rem;
  padding-top: 1rem;
}

.default-reasoning-effort-field {
  max-width: 28rem;
}

.default-reasoning-effort-select {
  min-height: 2.5rem;
}

.thinking-option-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border: 1px solid rgb(226 232 240);
  border-radius: 0.75rem;
  background: white;
  padding: 0.75rem 0.85rem;
}

.thinking-option-card .thinking-mode-capsule {
  flex-shrink: 0;
}

.thinking-effort-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin-top: 0.65rem;
}

.thinking-effort-option {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  min-width: 0;
  min-height: 4.5rem;
  border: 1px solid rgb(226 232 240);
  border-radius: 0.65rem;
  background: white;
  padding: 0.7rem;
  color: rgb(51 65 85);
  font-size: 0.875rem;
  transition: border-color 150ms, background-color 150ms, box-shadow 150ms;
}

.thinking-effort-option:hover {
  border-color: rgb(147 197 253);
}

.thinking-effort-option-selected {
  border-color: rgb(96 165 250);
  background: rgb(239 246 255);
  box-shadow: 0 1px 2px rgba(37, 99, 235, 0.08);
}

.thinking-effort-label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.15rem;
}

.thinking-effort-description {
  color: rgb(100 116 139);
  font-size: 0.75rem;
  line-height: 1.35;
}

@media (max-width: 640px) {
  .thinking-provider-tip {
    flex-direction: column;
    gap: 0.35rem;
  }

  .thinking-options-grid,
  .thinking-effort-options {
    grid-template-columns: 1fr;
  }
}

.provider-menu-item:hover,
.model-picker-item:hover {
  background: rgb(239 246 255);
}

.provider-menu-item-active {
  background: rgb(239 246 255);
  color: rgb(29 78 216);
}

.model-picker-menu {
  left: 0;
  right: 0;
  top: calc(100% + 0.35rem);
  max-height: 15rem;
  overflow-y: auto;
}

.model-picker-item {
  flex-direction: column;
  align-items: flex-start;
  gap: 0.15rem;
  border-radius: 0;
  padding: 0.65rem 0.75rem;
}

.model-picker-close {
  display: inline-flex;
  width: 1.5rem;
  height: 1.5rem;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  color: rgb(100 116 139);
  font-size: 1.25rem;
  line-height: 1;
}

.model-picker-close:hover {
  background: rgb(241 245 249);
  color: rgb(30 41 59);
}

.discover-model-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.3rem;
  min-width: 7.75rem;
  border-radius: 0.45rem;
  background: rgb(239 246 255);
  padding: 0.35rem 0.55rem;
  color: rgb(37 99 235);
  font-size: 0.75rem;
  font-weight: 600;
}

.discover-model-button:hover {
  background: rgb(219 234 254);
}

.discover-model-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.status-switch {
  display: inline-flex;
  width: 2.25rem;
  height: 1.25rem;
  align-items: center;
  border-radius: 9999px;
  padding: 0.125rem;
  transition: background-color 150ms;
}

.status-switch-on {
  justify-content: flex-end;
  background: rgb(34 197 94);
}

.status-switch-off {
  justify-content: flex-start;
  background: rgb(203 213 225);
}

.status-switch-knob {
  display: block;
  width: 1rem;
  height: 1rem;
  border-radius: 9999px;
  background: white;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.25);
}
</style>
