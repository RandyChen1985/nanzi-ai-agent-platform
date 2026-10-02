<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { metadataApi } from '../../api/metadata'
import type { Metric } from '../../api/metadata'
import { useUser } from '../../composables/useUser'
import { SparklesIcon } from '@heroicons/vue/24/outline'

const { isAdmin: _isAdmin, hasPermission } = useUser()

const props = defineProps<{
  datasetId: number
}>()

const emit = defineEmits(['show-smart-discovery'])

const metrics = ref<Metric[]>([])
const loading = ref(false)
const showModal = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const deleteId = ref<number | null>(null)

// UI State
const error = ref('')
const modalError = ref('')

const form = ref<Metric>({
  name: '',
  display_name: '',
  description: '',
  calculation_logic: '',
  unit: '',
  tags: []
})

const tagInput = ref('')

const addTag = (target: { tags?: string[] }) => {
  const tag = tagInput.value.trim()
  if (!tag) return
  const tags = (target.tags = target.tags || [])
  if (!tags.includes(tag)) {
    tags.push(tag)
    tagInput.value = ''
  }
}

const removeTag = (target: { tags?: string[] }, index: number) => {
  if (!target.tags) return
  target.tags.splice(index, 1)
}

const fetchMetrics = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await metadataApi.getMetrics(props.datasetId)
    metrics.value = res.data
  } catch (e) {
    console.error(e)
    error.value = '无法加载指标列表'
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editingId.value = null
  form.value = { name: '', display_name: '', description: '', calculation_logic: '', unit: '', tags: [] }
  tagInput.value = ''
  modalError.value = ''
  showModal.value = true
}

const openEdit = (m: Metric) => {
  editingId.value = m.id || null
  form.value = { ...m, tags: m.tags || [] }
  tagInput.value = ''
  modalError.value = ''
  showModal.value = true
}

const handleDelete = (id: number) => {
  deleteId.value = id
}

const selectedMetricIds = ref<number[]>([])
const showBatchDeleteModal = ref(false)
const batchDeleting = ref(false)

// 视图模式：卡片网格 (grid) 或 结构化列表 (list)
const viewMode = ref<'grid' | 'list'>((localStorage.getItem('nanzi_metric_view_mode') as 'grid' | 'list') || 'grid')
const setViewMode = (mode: 'grid' | 'list') => {
  viewMode.value = mode
  localStorage.setItem('nanzi_metric_view_mode', mode)
}

const isAllMetricsSelected = computed(() => {
  if (metrics.value.length === 0) return false
  return metrics.value.every(m => m.id && selectedMetricIds.value.includes(m.id))
})

const isSomeMetricsSelected = computed(() => {
  return metrics.value.some(m => m.id && selectedMetricIds.value.includes(m.id)) && !isAllMetricsSelected.value
})

const toggleSelectAllMetrics = () => {
  if (isAllMetricsSelected.value) {
    selectedMetricIds.value = []
  } else {
    selectedMetricIds.value = metrics.value.map(m => m.id).filter(Boolean) as number[]
  }
}

const toggleSelectMetric = (id: number) => {
  const idx = selectedMetricIds.value.indexOf(id)
  if (idx > -1) {
    selectedMetricIds.value.splice(idx, 1)
  } else {
    selectedMetricIds.value.push(id)
  }
}

const handleBatchDelete = () => {
  if (selectedMetricIds.value.length === 0) return
  showBatchDeleteModal.value = true
}

const confirmBatchDelete = async () => {
  if (selectedMetricIds.value.length === 0) return
  batchDeleting.value = true
  try {
    await metadataApi.batchDeleteMetrics(selectedMetricIds.value)
    selectedMetricIds.value = []
    showBatchDeleteModal.value = false
    await fetchMetrics()
  } catch (e: any) {
    console.error('Batch delete metrics failed', e)
    error.value = '批量删除失败'
    setTimeout(() => error.value = '', 3000)
  } finally {
    batchDeleting.value = false
  }
}

const confirmDelete = async () => {
  if (!deleteId.value) return
  try {
    await metadataApi.deleteMetric(deleteId.value)
    selectedMetricIds.value = selectedMetricIds.value.filter(id => id !== deleteId.value)
    deleteId.value = null
    fetchMetrics()
  } catch (e) {
    console.error(e)
    error.value = '删除失败'
    setTimeout(() => error.value = '', 3000)
    deleteId.value = null
  }
}

const handleSave = async () => {
  modalError.value = ''
  if (!form.value.name || !form.value.display_name) {
    modalError.value = '请填写必要信息 (ID,从名称)'
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await metadataApi.updateMetric(editingId.value, form.value)
    } else {
      await metadataApi.createMetric(props.datasetId, form.value)
    }
    showModal.value = false
    fetchMetrics()
  } catch (e: any) {
    console.error(e)
    modalError.value = '保存失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

watch(() => props.datasetId, fetchMetrics)
onMounted(fetchMetrics)

defineExpose({ fetchMetrics })
</script>

<template>
  <div class="space-y-3">
    <!-- Toolbar -->
    <div class="flex flex-wrap justify-between items-center gap-3 bg-white p-3 rounded-xl border border-gray-100 shadow-sm">
      <div class="flex items-center gap-3">
        <label v-if="metrics.length > 0 && hasPermission('element:metadata:edit')" class="flex items-center gap-2 cursor-pointer select-none text-xs font-bold text-gray-600 hover:text-gray-900 shrink-0 ml-1">
          <input 
            type="checkbox" 
            :checked="isAllMetricsSelected"
            :indeterminate="isSomeMetricsSelected"
            @change="toggleSelectAllMetrics"
            class="w-4 h-4 text-amber-600 rounded border-gray-300 focus:ring-amber-500 cursor-pointer"
          />
          <span>全选</span>
        </label>
        <h3 class="text-base font-bold text-gray-800">业务指标 (Metrics)</h3>
        <span class="text-xs text-gray-400 font-medium hidden sm:inline">共 {{ metrics.length }} 个指标</span>
      </div>

      <div class="flex items-center gap-3">
        <div v-if="selectedMetricIds.length > 0 && hasPermission('element:metadata:edit')" class="flex items-center gap-2">
          <span class="text-xs bg-amber-50 text-amber-700 px-2.5 py-1 rounded-full font-bold border border-amber-200">
            已选择 {{ selectedMetricIds.length }} 项
          </span>
          <button 
            @click="handleBatchDelete"
            class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 text-xs font-bold shadow-sm"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            批量删除
          </button>
        </div>

        <!-- View Switcher -->
        <div class="flex items-center bg-gray-100 p-0.5 rounded-lg border border-gray-200">
          <button 
            type="button"
            @click="setViewMode('grid')" 
            :class="['p-1.5 rounded-md transition-all flex items-center justify-center', viewMode === 'grid' ? 'bg-white text-amber-600 shadow-sm font-bold' : 'text-gray-400 hover:text-gray-600']"
            title="卡片网格视图"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>
          </button>
          <button 
            type="button"
            @click="setViewMode('list')" 
            :class="['p-1.5 rounded-md transition-all flex items-center justify-center', viewMode === 'list' ? 'bg-white text-amber-600 shadow-sm font-bold' : 'text-gray-400 hover:text-gray-600']"
            title="表格列表视图"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>
          </button>
        </div>

        <template v-if="hasPermission('element:metadata:edit')">
          <button 
            @click="emit('show-smart-discovery')"
            class="bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 px-3.5 py-2 rounded-lg transition-all flex items-center gap-2 text-xs font-bold shadow-sm h-9"
          >
            <svg class="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
            <SparklesIcon class="w-4 h-4" /> 智能发现指标
          </button>
          <button 
            @click="openCreate"
            class="bg-amber-500 hover:bg-amber-600 text-white px-4 py-2 rounded-lg transition-all shadow-md flex items-center gap-2 text-xs font-bold h-9"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
            新建指标
          </button>
        </template>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-if="error" class="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm mb-4 border border-red-100 flex items-center gap-2">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
      {{ error }}
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex justify-center py-8">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-500"></div>
    </div>
    
    <!-- Empty State -->
    <div v-else-if="metrics.length === 0" class="text-center py-12 bg-amber-50/50 rounded-xl border border-dashed border-amber-200">
      <p class="text-amber-600 font-medium">暂无定义的指标</p>
      <p class="text-xs text-amber-500 mt-1">添加指标以帮助 AI 理解复杂的业务计算逻辑。</p>
    </div>

    <!-- Grid Card View -->
    <div v-else-if="viewMode === 'grid'" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
       <div v-for="m in metrics" :key="m.id" :class="['bg-white p-5 rounded-xl border transition-all group relative', m.id && selectedMetricIds.includes(m.id) ? 'border-amber-400 ring-2 ring-amber-100/80 shadow-md' : 'border-gray-200 shadow-sm hover:shadow-md']">
          <div class="absolute top-4 right-4 flex items-center gap-2">
             <div class="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity" v-if="hasPermission('element:metadata:edit')">
                <button @click="openEdit(m)" class="text-gray-400 hover:text-blue-500"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg></button>
                <button @click="handleDelete(m.id!)" class="text-gray-400 hover:text-red-500"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg></button>
             </div>
             <div v-if="hasPermission('element:metadata:edit') && m.id" class="ml-1" @click.stop>
                <input 
                  type="checkbox" 
                  :checked="selectedMetricIds.includes(m.id)"
                  @change="toggleSelectMetric(m.id)"
                  class="w-4 h-4 text-amber-600 rounded border-gray-300 focus:ring-amber-500 cursor-pointer"
                />
             </div>
          </div>
          
          <div class="flex items-center gap-2 mb-2 pr-12">
             <div class="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center text-amber-600 font-bold text-xs shrink-0">M</div>
             <div class="min-w-0">
                <h4 class="font-bold text-gray-900 truncate">{{ m.display_name }}</h4>
                <p class="text-xs font-mono text-gray-500 truncate">{{ m.name }}</p>
             </div>
          </div>
          
          <div class="space-y-2 mt-3">
             <div class="text-xs bg-gray-50 p-2 rounded border border-gray-100 font-mono text-gray-600 break-all">
                {{ m.calculation_logic || '--' }}
             </div>
             <p class="text-xs text-gray-500 line-clamp-2 h-8">{{ m.description || '无描述' }}</p>
             <div v-if="m.unit" class="text-xs text-gray-400 flex items-center gap-1">
                <span>Unit:</span> <span class="bg-gray-100 px-1 rounded text-gray-600">{{ m.unit }}</span>
             </div>
             <div v-if="m.tags && m.tags.length" class="flex flex-wrap gap-1 pt-1">
                <span v-for="tag in m.tags" :key="tag" class="px-1.5 py-0.5 bg-blue-50 text-blue-700 text-[10px] rounded-full border border-blue-100">{{ tag }}</span>
             </div>
          </div>
       </div>
    </div>

    <!-- Table List View -->
    <div v-else-if="viewMode === 'list'" class="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="bg-gray-50/80 border-b border-gray-100 text-gray-500 font-bold uppercase tracking-wider">
              <th v-if="hasPermission('element:metadata:edit')" class="px-4 py-3 w-10 text-center">
                <input 
                  type="checkbox" 
                  :checked="isAllMetricsSelected"
                  :indeterminate="isSomeMetricsSelected"
                  @change="toggleSelectAllMetrics"
                  class="w-4 h-4 text-amber-600 rounded border-gray-300 focus:ring-amber-500 cursor-pointer"
                />
              </th>
              <th class="px-4 py-3 w-1/4">指标名称 / 物理标识</th>
              <th class="px-4 py-3 w-1/3">计算逻辑</th>
              <th class="px-4 py-3">业务描述</th>
              <th class="px-4 py-3 w-20 text-center">单位</th>
              <th class="px-4 py-3">标签</th>
              <th v-if="hasPermission('element:metadata:edit')" class="px-4 py-3 w-24 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100 text-gray-700">
            <tr 
              v-for="m in metrics" 
              :key="m.id" 
              :class="['hover:bg-amber-50/40 transition-colors group', m.id && selectedMetricIds.includes(m.id) ? 'bg-amber-50/60' : '']"
            >
              <td v-if="hasPermission('element:metadata:edit')" class="px-4 py-3 text-center" @click.stop>
                <input 
                  v-if="m.id"
                  type="checkbox" 
                  :checked="selectedMetricIds.includes(m.id)"
                  @change="toggleSelectMetric(m.id)"
                  class="w-4 h-4 text-amber-600 rounded border-gray-300 focus:ring-amber-500 cursor-pointer"
                />
              </td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-2.5">
                  <div class="w-7 h-7 rounded-lg bg-amber-100 flex items-center justify-center text-amber-700 font-bold text-xs shrink-0">M</div>
                  <div class="min-w-0">
                    <div class="font-bold text-gray-900 truncate">{{ m.display_name }}</div>
                    <div class="text-[11px] font-mono text-gray-400 truncate">#{{ m.name }}</div>
                  </div>
                </div>
              </td>
              <td class="px-4 py-3">
                <div class="font-mono text-gray-600 bg-gray-50 px-2 py-1 rounded border border-gray-100 text-[11px] line-clamp-2 max-w-md break-all" :title="m.calculation_logic">
                  {{ m.calculation_logic || '--' }}
                </div>
              </td>
              <td class="px-4 py-3 text-gray-500 max-w-xs truncate" :title="m.description">
                {{ m.description || '暂无描述' }}
              </td>
              <td class="px-4 py-3 text-center">
                <span v-if="m.unit" class="bg-gray-100 text-gray-600 px-2 py-0.5 rounded text-[11px] font-medium">{{ m.unit }}</span>
                <span v-else class="text-gray-300">-</span>
              </td>
              <td class="px-4 py-3">
                <div v-if="m.tags && m.tags.length" class="flex flex-wrap gap-1 max-w-xs">
                  <span v-for="tag in m.tags" :key="tag" class="px-1.5 py-0.5 bg-blue-50 text-blue-700 text-[10px] rounded-full border border-blue-100">{{ tag }}</span>
                </div>
                <span v-else class="text-gray-300">-</span>
              </td>
              <td v-if="hasPermission('element:metadata:edit')" class="px-4 py-3 text-right">
                <div class="flex items-center justify-end gap-1 opacity-80 group-hover:opacity-100 transition-opacity">
                  <button @click="openEdit(m)" class="text-gray-400 hover:text-blue-600 p-1.5 hover:bg-white rounded-md transition-colors" title="编辑指标">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
                  </button>
                  <button @click="handleDelete(m.id!)" class="text-gray-400 hover:text-red-600 p-1.5 hover:bg-white rounded-md transition-colors" title="删除指标">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Modal -->
    <Teleport to="body">
      <div v-if="showModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showModal = false">
         <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-100 animate-fade-in-up">
            <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-amber-50">
               <h3 class="font-bold text-gray-900">{{ editingId ? '编辑指标' : '新建指标' }}</h3>
               <button @click="showModal = false" class="text-gray-400 hover:text-gray-600">&times;</button>
            </div>
          
            <!-- Modal Error -->
            <div v-if="modalError" class="px-6 pt-4 pb-0">
               <div class="bg-red-50 text-red-600 px-3 py-2 rounded text-xs border border-red-100">
                  {{ modalError }}
               </div>
            </div>
            <div class="p-6 space-y-4">
               <div class="grid grid-cols-2 gap-4">
                  <div>
                     <label class="block text-sm font-medium text-gray-700 mb-1">物理标识 (ID)</label>
                     <input v-model="form.name" :disabled="!!editingId" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. pue">
                  </div>
                  <div>
                     <label class="block text-sm font-medium text-gray-700 mb-1">显示名称</label>
                     <input v-model="form.display_name" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. PUE值">
                  </div>
               </div>
               <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">计算逻辑</label>
                   <textarea v-model="form.calculation_logic" rows="3" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. total_power / it_power"></textarea>
                   <p class="text-xs text-gray-400 mt-1">支持 SQL 表达式或自然语言描述。</p>
               </div>
               <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">业务描述</label>
                  <textarea v-model="form.description" rows="2" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none"></textarea>
               </div>
               <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">单位</label>
                  <input v-model="form.unit" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. kWh, %">
               </div>
               <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">标签 <span class="text-gray-400 font-normal">(用于业务归类与检索，可选)</span></label>
                  <input v-model="tagInput" @keyup.enter="addTag(form)" type="text" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="输入标签后回车">
                  <div class="flex flex-wrap gap-2 mt-2">
                    <span v-for="(tag, i) in form.tags" :key="i" class="px-2 py-1 bg-blue-50 text-blue-700 text-xs rounded-full flex items-center gap-1 border border-blue-100">
                      {{ tag }}
                      <button type="button" @click="removeTag(form, i as number)" class="text-blue-400 hover:text-red-500 ml-1" title="移除标签">&times;</button>
                    </span>
                    <span v-if="!form.tags || form.tags.length === 0" class="text-xs text-gray-400">暂无标签</span>
                  </div>
               </div>
            </div>
            <div class="p-6 bg-gray-50 flex justify-end gap-3">
               <button @click="showModal = false" class="px-4 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50">取消</button>
               <button @click="handleSave" :disabled="saving" class="px-6 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded-lg text-sm font-bold shadow-md disabled:opacity-50">保存</button>
            </div>
         </div>
      </div>
    </Teleport>
    <!-- Delete Modal -->
    <Teleport to="body">
      <div v-if="deleteId" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="deleteId = null">
         <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
            <div class="p-6 text-center">
               <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                  <svg class="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
               </div>
               <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除?</h3>
               <p class="text-sm text-gray-500 mb-6">
                 您确定要删除此指标吗？<br>此操作无法撤销。
               </p>
               <div class="flex gap-3 justify-center">
                  <button @click="deleteId = null" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                  <button @click="confirmDelete" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30">确认删除</button>
               </div>
            </div>
         </div>
      </div>
    </Teleport>
    <!-- Batch Delete Modal -->
    <Teleport to="body">
      <div v-if="showBatchDeleteModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showBatchDeleteModal = false">
         <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
            <div class="p-6 text-center">
               <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                  <svg class="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
               </div>
               <h3 class="text-lg font-bold text-gray-900 mb-2">确认批量删除指标?</h3>
               <p class="text-sm text-gray-500 mb-6">
                 您确定要删除已选中的 <b class="text-red-600">{{ selectedMetricIds.length }}</b> 个指标吗？<br>此操作无法撤销。
               </p>
               <div class="flex gap-3 justify-center">
                  <button @click="showBatchDeleteModal = false" :disabled="batchDeleting" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                  <button @click="confirmBatchDelete" :disabled="batchDeleting" class="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30 disabled:opacity-50 flex items-center gap-2">
                     <svg v-if="batchDeleting" class="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                     <span>{{ batchDeleting ? '正在删除...' : '确认批量删除' }}</span>
                  </button>
               </div>
            </div>
         </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
