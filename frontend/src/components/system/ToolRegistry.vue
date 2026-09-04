<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
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

const toolForm = ref<Partial<SysApiToolCreate> & { id?: string; parameter_schema_str: string; headers_str: string }>({
  name: '',
  description: '',
  method: 'GET',
  url_template: '',
  headers_str: '{}',
  parameter_schema_str: '{}',
  is_active: true
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
        return matchesKeyword && matchesMethod && matchesStatus
    })
})

const toolMethodOptions = computed(() => Array.from(new Set([
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS',
    ...tools.value.map((tool) => String(tool.method || '').toUpperCase()).filter(Boolean),
])))

const hasToolFilters = computed(() => Boolean(
    toolSearchQuery.value.trim()
    || toolMethodFilter.value !== 'all'
    || toolStatusFilter.value !== 'all'
))

const clearToolFilters = () => {
    toolSearchQuery.value = ''
    toolMethodFilter.value = 'all'
    toolStatusFilter.value = 'all'
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
            is_active: true
        }
    }
    showModal.value = true
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
            is_active: toolForm.value.is_active
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
         <div class="p-4 border-b border-gray-100 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h3 class="text-lg font-medium text-gray-900">API 工具注册表</h3>
            <div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
                <input
                    v-model="toolSearchQuery"
                    type="search"
                    placeholder="搜索名称、描述或 URL..."
                    class="w-full sm:w-56 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <select v-model="toolMethodFilter" class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按 HTTP 方法筛选">
                    <option value="all">Method：全部</option>
                    <option v-for="method in toolMethodOptions" :key="method" :value="method">{{ method }}</option>
                </select>
                <select v-model="toolStatusFilter" class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20" title="按状态筛选">
                    <option value="all">状态：全部</option>
                    <option value="active">启用</option>
                    <option value="inactive">停用</option>
                </select>
                <button
                    type="button"
                    class="px-2.5 py-2 text-sm text-gray-500 hover:text-primary"
                    :class="{ invisible: !hasToolFilters }"
                    :disabled="!hasToolFilters"
                    :tabindex="hasToolFilters ? 0 : -1"
                    @click="clearToolFilters"
                >清空</button>
                <button
                    v-if="canSave"
                    @click="openModal()"
                    class="px-3 py-2 bg-primary text-white text-sm rounded-md hover:bg-primary-dark transition-colors"
                >
                    + 添加工具
                </button>
            </div>
         </div>
         
         <div v-if="loading" class="p-8 text-center text-gray-400">加载中...</div>
         <div v-else class="overflow-x-auto">
         <table class="min-w-[900px] w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">名称</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Method</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">URL Template</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">状态</th>
                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">操作</th>
                </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
                <tr v-for="t in filteredTools" :key="t.id" class="hover:bg-gray-50">
                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                        {{ t.name }}
                        <p class="text-xs text-gray-500 font-normal truncate max-w-xs">{{ t.description }}</p>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full" 
                            :class="{'bg-green-100 text-green-800': t.method === 'GET', 'bg-blue-100 text-blue-800': t.method === 'POST', 'bg-yellow-100 text-yellow-800': t.method === 'PUT', 'bg-red-100 text-red-800': t.method === 'DELETE'}"
                        >
                            {{ t.method }}
                        </span>
                    </td>
                    <td class="px-6 py-4 text-sm text-gray-500 font-mono truncate max-w-sm" :title="t.url_template">
                        {{ t.url_template }}
                    </td>
                     <td class="px-6 py-4 whitespace-nowrap">
                        <span v-if="t.is_active" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">启用</span>
                        <span v-else class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">停用</span>
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
                <tr v-if="filteredTools.length === 0">
                    <td colspan="5" class="px-6 py-8 text-center text-gray-400 text-sm">{{ hasToolFilters ? '暂无匹配工具' : '暂无工具配置' }}</td>
                </tr>
            </tbody>
         </table>
         </div>
      </div>

      <!-- Modal -->
      <div v-if="showModal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
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
                     <label class="block text-sm font-medium text-gray-700">参数定义 (JSON Schema)</label>
                     <textarea v-model="toolForm.parameter_schema_str" rows="5" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono text-xs bg-gray-50" placeholder='{ "city": { "type": "string", "description": "城市名" } }'></textarea>
                     <p class="text-xs text-gray-500 mt-1">定义参数类型和描述，用于 LLM 理解</p>
                  </div>
                  
                  <div>
                     <label class="block text-sm font-medium text-gray-700">Headers (JSON)</label>
                     <textarea v-model="toolForm.headers_str" rows="3" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono text-xs bg-gray-50" placeholder='{ "Authorization": "Bearer token" }'></textarea>
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
