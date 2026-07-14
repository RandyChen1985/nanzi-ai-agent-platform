<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { modelApi, type AIModel, type AIModelCreate } from '../../api/model'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import ConfirmModal from '../ConfirmModal.vue'
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
const testingModelId = ref<string | null>(null)
const showModelModal = ref(false)
const isEditingModel = ref(false)
const showDeleteConfirm = ref(false)
const pendingDeleteModel = ref<AIModel | null>(null)
const modelForm = ref<Partial<AIModelCreate> & { id?: string }>({
  name: '',
  model_id: '',
  provider: 'openai',
  type: 'llm',
  api_base_url: '',
  api_key: '',
  is_active: true
})

const fetchModels = async () => {
    loadingModels.value = true
    try {
        const res = await modelApi.list()
        models.value = res.data
    } catch (e: any) {
        showToast('获取模型列表失败', 'error')
    } finally {
        loadingModels.value = false
    }
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

const openModelModal = (model?: AIModel, isClone = false) => {
    if (model) {
        if (isClone) {
            isEditingModel.value = false
            modelForm.value = { 
                ...model, 
                id: undefined, 
                name: `${model.name} (Copy)`,
                model_id: `${model.model_id}-copy`,
                api_key: '' 
            }
        } else {
            isEditingModel.value = true
            modelForm.value = { ...model, api_key: '' }
        }
    } else {
        isEditingModel.value = false
        modelForm.value = {
            name: '',
            model_id: '',
            provider: 'openai',
            type: 'llm',
            api_base_url: '',
            api_key: '',
            is_active: true
        }
    }
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
    
    try {
        const payload = { ...modelForm.value }
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
        showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
    }
}

const deleteModel = (model: AIModel) => {
    pendingDeleteModel.value = model
    showDeleteConfirm.value = true
}

const closeDeleteConfirm = () => {
    showDeleteConfirm.value = false
    pendingDeleteModel.value = null
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

// Expose refresh to parent if needed
defineExpose({ refresh: fetchModels })

onMounted(() => {
  fetchModels()
})
</script>

<template>
  <div class="h-full overflow-y-auto pb-6 custom-scrollbar p-1">
      <div class="bg-white shadow rounded-lg overflow-hidden">
         <div class="p-4 border-b border-gray-100 flex justify-between items-center">
            <h3 class="text-lg font-medium text-gray-900">AI 模型注册表</h3>
            <button 
                v-if="canSave"
                @click="openModelModal()"
                class="px-3 py-1.5 bg-primary text-white text-sm rounded-md hover:bg-primary-dark transition-colors"
            >
                + 添加模型
            </button>
         </div>
         
         <div v-if="loadingModels" class="p-8 text-center text-gray-400">加载中...</div>
         <table v-else class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">显示名称</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">模型 ID (API)</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">提供商</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">类型</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">状态</th>
                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">操作</th>
                </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
                <tr v-for="m in models" :key="m.id" class="hover:bg-gray-50">
                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{{ m.name }}</td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500 font-mono">{{ m.model_id }}</td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-blue-100 text-blue-800">
                            {{ m.provider }}
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
                        <span v-if="m.is_active" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">启用</span>
                        <span v-else class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">停用</span>
                     </td>
                     <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
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
                <tr v-if="models.length === 0">
                    <td colspan="6" class="px-6 py-8 text-center text-gray-400 text-sm">暂无模型配置</td>
                </tr>
            </tbody>
         </table>
      </div>

      <!-- Model Modal (Moved inside component for self-containment) -->
      <div v-if="showModelModal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
          <div class="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 space-y-4 text-left">
              <h3 class="text-lg font-bold text-gray-900">{{ isEditingModel ? '编辑模型' : '添加新模型' }}</h3>
              
              <div class="space-y-3">
                  <div>
                     <label class="block text-sm font-medium text-gray-700">显示名称</label>
                     <input v-model="modelForm.name" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="例如: GPT-4o 生产版" />
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">模型 ID (API)</label>
                     <input v-model="modelForm.model_id" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm font-mono" placeholder="例如: gpt-4o" />
                     <p class="text-xs text-gray-500 mt-1">云服务商定义的实际模型标识符</p>
                  </div>
                  <div class="grid grid-cols-2 gap-4">
                     <div>
                         <label class="block text-sm font-medium text-gray-700">提供商</label>
                         <select v-model="modelForm.provider" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                             <option value="openai">OpenAI</option>
                             <option value="azure">Azure OpenAI</option>
                             <option value="anthropic">Anthropic</option>
                             <option value="google">Google Gemini</option>
                             <option value="ollama">Ollama (Local)</option>
                             <option value="other">Other</option>
                         </select>
                     </div>
                     <div>
                         <label class="block text-sm font-medium text-gray-700">类型</label>
                         <select v-model="modelForm.type" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                             <option value="llm">LLM (文本生成)</option>
                             <option value="embedding">Embedding (向量)</option>
                             <option value="multimodal">Multimodal (多模态)</option>
                         </select>
                     </div>
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">API Base URL (可选)</label>
                     <input v-model="modelForm.api_base_url" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="如需覆盖系统默认配置" />
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700">API Key (可选)</label>
                     <input v-model="modelForm.api_key" type="password" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm" placeholder="留空则使用默认/此时不修改" />
                  </div>
                  <div class="flex items-center">
                      <input id="is_active" type="checkbox" v-model="modelForm.is_active" class="h-4 w-4 text-primary focus:ring-primary border-gray-300 rounded" />
                      <label for="is_active" class="ml-2 block text-sm text-gray-900">启用此模型</label>
                  </div>
              </div>
              
              <div class="flex justify-end space-x-3 mt-6">
                  <button @click="showModelModal = false" class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">取消</button>
                  <button @click="saveModel" class="px-4 py-2 bg-primary border border-transparent rounded-md text-sm font-medium text-white hover:bg-primary-dark">保存</button>
              </div>
          </div>
      </div>

      <ConfirmModal
        v-if="showDeleteConfirm && pendingDeleteModel"
        title="删除模型"
        :message="`确定要删除模型「${pendingDeleteModel.name}」吗？删除后将无法恢复。`"
        confirm-text="删除"
        cancel-text="取消"
        type="danger"
        @confirm="confirmDeleteModel"
        @cancel="closeDeleteConfirm"
      />
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.5);
}
</style>
