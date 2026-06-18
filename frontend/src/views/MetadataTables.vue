<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { metadataApi } from '../api/metadata'
import type { Dataset, Table } from '../api/metadata'

import MetricList from '../components/metadata/MetricList.vue'
import RelationshipList from '../components/metadata/RelationshipList.vue'
import SmartImportWizard from '../components/metadata/SmartImportWizard.vue'
import SmartMetricModal from '../components/metadata/SmartMetricModal.vue'
import SchemaGraph from '../components/metadata/SchemaGraph.vue'
import ChangelogList from '../components/metadata/ChangelogList.vue'
import { useUser } from '../composables/useUser'
import { useToast } from '../composables/useToast'

const { isAdmin: _isAdmin, hasPermission } = useUser()
const { showToast } = useToast()

const route = useRoute()
const datasetId = Number(route.params.id)

const dataset = ref<Dataset | null>(null)
const tables = ref<Table[]>([])
const loading = ref(false)
const showImportModal = ref(false)
const showFullDescription = ref(false)
const showMetricModal = ref(false)
const showYamlModal = ref(false)
const showEditModal = ref(false) 
const showCreateTableModal = ref(false)
const yamlContent = ref('')

const newTable = ref<Table>({
  physical_name: '',
  term: '',
  description: '',
  columns: []
})

const openCreateTableModal = () => {
  newTable.value = {
    physical_name: '',
    term: '',
    description: '',
    columns: [
      { physical_name: 'id', term: '主键ID', type: 'Int64', description: '唯一标识', is_primary: true }
    ]
  }
  showCreateTableModal.value = true
}

const handleCreateTable = async () => {
  if (!newTable.value.physical_name || !newTable.value.term) {
    showToast('请填写表物理名和业务名', 'warning')
    return
  }
  try {
    await metadataApi.saveTable(datasetId, newTable.value)
    showCreateTableModal.value = false
    fetchDatasetInfo()
  } catch (e) {
    console.error('Create table failed', e)
  }
}

// Search and Filter
const searchQuery = ref('')
const filteredTables = computed(() => {
  if (!searchQuery.value) return tables.value
  const q = searchQuery.value.toLowerCase()
  return tables.value.filter(t => 
    t.physical_name.toLowerCase().includes(q) || 
    (t.term && t.term.toLowerCase().includes(q)) ||
    (t.description && t.description.toLowerCase().includes(q))
  )
})

// Tabs
const activeTab = ref('tables')

// Edit Mode
const editingTable = ref<Table | null>(null)
const deleteTableId = ref<string | null>(null)

const fetchDatasetInfo = async () => {
  loading.value = true
  try {
    const res = await metadataApi.getDataset(datasetId)
    dataset.value = res.data
    tables.value = res.data.tables || []
  } catch (e) {
    console.error('Failed to fetch dataset', e)
  } finally {
    loading.value = false
  }
}

const openEditModal = (table: Table) => {
  // Deep copy to avoid modifying original list before save
  editingTable.value = JSON.parse(JSON.stringify(table))
  showEditModal.value = true
}

const handleUpdateTable = async () => {
  if (!editingTable.value) return
  try {
    await metadataApi.saveTable(datasetId, editingTable.value)
    showEditModal.value = false
    editingTable.value = null
    fetchDatasetInfo() // Reload data
  } catch (e) {
    console.error('Update failed', e)
  }
}

const handleDeleteTable = (tableName: string) => {
  deleteTableId.value = tableName
}

const confirmDeleteTable = async () => {
  if (!deleteTableId.value) return
  try {
    // We don't have a direct delete table API yet, but we can assume one exists or implement it.
    // Based on the current API structure, we might need to add it to metadataApi.
    await metadataApi.deleteTable(datasetId, deleteTableId.value)
    fetchDatasetInfo()
    deleteTableId.value = null
  } catch (e) {
    console.error('Delete table failed', e)
    deleteTableId.value = null
  }
}

const addColumn = () => {
  if (!editingTable.value) return
  editingTable.value.columns.push({
    physical_name: '',
    term: '',
    type: 'String',
    description: '',
    enums: [],
    synonyms: []
  })
}

const removeColumn = (index: number) => {
  if (!editingTable.value) return
  editingTable.value.columns.splice(index, 1)
}

const fetchYaml = async () => {
  try {
    const res = await metadataApi.getDatasetYaml(datasetId)
    yamlContent.value = res.data.data
    showYamlModal.value = true
  } catch (e) {
    console.error('Failed to fetch YAML', e)
  }
}

// saveTables and handleAnalyze are removed as they are handled by SmartImportWizard

const copyYaml = async (event: Event) => {
  try {
    await navigator.clipboard.writeText(yamlContent.value)
    
    // Visual feedback
    const btn = event.currentTarget as HTMLElement
    const originalHtml = btn.innerHTML
    const originalClasses = Array.from(btn.classList)

    btn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg> 已复制`
    btn.classList.add('bg-green-50', 'text-green-600', 'border-green-200')
    btn.classList.remove('bg-white', 'text-gray-700', 'border-gray-200')

    setTimeout(() => {
        btn.innerHTML = originalHtml
        btn.classList.add(...originalClasses)
        btn.classList.remove('bg-green-50', 'text-green-600', 'border-green-200')
    }, 2000)
  } catch (err) {
    console.error('Failed to copy', err)
  }
}

const getDatasetEmoji = (name: string) => {
  const emojis = ['📊', '📈', '💿', '🗄️', '🧠', '🧊', '🌊', '⚡', '📅', '🛒', '👥', '🔗', '📦', '🏷️', '💎']
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  const index = Math.abs(hash) % emojis.length
  return emojis[index]
}

const syncingId = ref<number | null>(null)

const handleSyncNow = async () => {
  if (!dataset.value || syncingId.value) return
  
  syncingId.value = dataset.value.id
  try {
    const res: any = await metadataApi.syncToRag(dataset.value.id)
    if (res.data.code === 200) {
      // Optimistic update
      dataset.value.rag_sync_status = 1
      showToast('同步任务已启动，请稍后刷新查看状态', 'success')
      // Auto refresh after a short delay
      setTimeout(fetchDatasetInfo, 3500)
    } else {
      showToast(res.data.message || '同步请求失败', 'error')
    }
  } catch (e: any) {
    const errorMsg = e.response?.data?.message || e.message || '启动同步失败'
    showToast(errorMsg, 'error')
  } finally {
    // Keep the loading state for at least 1 second for visual comfort
    setTimeout(() => {
        syncingId.value = null
    }, 1000)
  }
}

onMounted(fetchDatasetInfo)

const metricListRef = ref<any>(null)

const fetchMetrics = () => {
  if (metricListRef.value && typeof metricListRef.value.fetchMetrics === 'function') {
    metricListRef.value.fetchMetrics()
  }
}

defineExpose({ fetchMetrics })
</script>

<template>
  <div class="space-y-6">
    <!-- Breadcrumbs -->
    <nav class="flex mb-4 text-sm" aria-label="Breadcrumb">
      <ol class="flex items-center space-x-2">
        <li>
          <router-link to="/dashboard/metadata" class="text-gray-400 hover:text-gray-600 transition-colors flex items-center gap-1">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>
            元数据管理
          </router-link>
        </li>
        <li class="flex items-center gap-2">
          <svg class="w-4 h-4 text-gray-300" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"></path></svg>
          <span class="text-gray-900 font-bold">{{ dataset?.display_name || '数据集详情' }}</span>
        </li>
      </ol>
    </nav>

    <!-- RAG Pending Warning Banner -->
    <div 
      v-if="dataset?.rag_sync_status === 3" 
      class="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-center justify-between shadow-sm animate-pulse-slow mb-6"
    >
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-full bg-amber-100 flex items-center justify-center text-amber-600 border border-amber-200">
           <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
        </div>
        <div>
          <h4 class="text-sm font-bold text-amber-900">元数据有更新，待同步到 RAGFlow</h4>
          <p class="text-[11px] text-amber-700 font-medium">当前 Agent 检索可能仍然使用旧版元数据。建议立即同步以保持语义一致。</p>
        </div>
      </div>
      <button 
        @click="hasPermission('element:metadata:sync') && handleSyncNow()"
        :disabled="syncingId === dataset?.id || !hasPermission('element:metadata:sync')"
        :title="!hasPermission('element:metadata:sync') ? '您没有同步到 RAGFlow 的权限' : '立即同步到 RAGFlow'"
        class="px-4 py-1.5 rounded-lg text-xs font-bold shadow-md transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
        :class="hasPermission('element:metadata:sync') 
          ? 'bg-amber-600 hover:bg-amber-700 text-white shadow-amber-500/20' 
          : 'bg-gray-200 text-gray-400 cursor-not-allowed shadow-none'"
      >
        <svg v-if="syncingId === dataset?.id" class="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
        <span>{{ syncingId === dataset?.id ? '正在同步...' : '立即同步' }}</span>
      </button>
    </div>

    <!-- Header Card -->
    <div class="flex justify-between items-start bg-white p-6 rounded-xl shadow-sm border border-gray-100 relative overflow-hidden">
      <!-- Decoration Top -->
      <div class="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-400 via-indigo-500 to-primary"></div>
      
      <div class="flex items-start gap-5 relative z-10">
        <div class="flex gap-4">
           <!-- Emoji -->
           <div class="w-16 h-16 rounded-xl bg-gray-50 flex items-center justify-center text-3xl shadow-inner border border-gray-100 flex-shrink-0">
              {{ dataset?.name ? getDatasetEmoji(dataset.name) : '📂' }}
           </div>
           
           <div class="max-w-2xl min-w-0">
              <div class="flex items-center gap-3">
                 <h1 class="text-2xl font-bold text-gray-900 leading-tight truncate">{{ dataset?.display_name || '加载中...' }}</h1>
                 <span class="px-2 py-0.5 bg-gray-100 text-gray-500 text-xs font-mono rounded select-all flex-shrink-0">#{{ dataset?.name }}</span>
              </div>
              
              <div class="mt-1 mb-2">
                <p class="text-gray-500 text-sm leading-relaxed">
                  {{ dataset?.description ? (dataset.description.length > 80 ? dataset.description.substring(0, 80) + '...' : dataset.description) : '暂无描述信息' }}
                  <button 
                    v-if="dataset?.description && dataset.description.length > 80" 
                    type="button"
                    @click.stop="showFullDescription = true"
                    class="text-primary font-bold hover:underline ml-1 inline-flex items-center"
                  >
                    [详情]
                  </button>
                </p>
              </div>
              
              <!-- Meta Info -->
              <div class="flex items-center gap-4 text-xs text-gray-400 font-mono">
                 <span class="flex items-center gap-1 bg-gray-50 px-2 py-0.5 rounded border border-gray-100">
                   <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2H6c-1.1 0-2 .9-2 2zm0 5h16"/></svg>
                   {{ dataset?.data_source }}
                 </span>
                 <span v-if="dataset?.updated_at" class="flex items-center gap-1">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    Updated {{ new Date(dataset.updated_at).toLocaleString() }}
                 </span>
              </div>
           </div>
        </div>
      </div>
      
      <div class="flex gap-3 mt-1 flex-shrink-0 ml-4">
        <button 
          v-has-perm="'element:metadata:view_yaml'"
          @click="fetchYaml"
          class="bg-white hover:bg-gray-50 text-gray-700 border border-gray-200 px-4 py-2 rounded-lg transition-all flex items-center gap-2 text-sm font-medium h-10 whitespace-nowrap"
        >
          <svg class="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
          查看 AI YAML
        </button>
        <button 
          v-if="hasPermission('element:metadata:import')"
          @click="showImportModal = true"
          class="bg-slate-900 hover:bg-black text-white px-4 py-2 rounded-lg transition-all shadow-md flex items-center gap-2 text-sm font-medium h-10 whitespace-nowrap"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
          导入 DDL 结构
        </button>
      </div>
    </div>



    <!-- Tabs -->
    <div class="border-b border-gray-200">
      <nav class="-mb-px flex space-x-8" aria-label="Tabs">
        <button
          @click="activeTab = 'tables'"
          :class="[activeTab === 'tables' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors duration-200 ease-in-out flex items-center gap-2']"
        >
          数据表 (Tables)
          <span 
            :class="[activeTab === 'tables' ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400', 'ml-1 py-0.5 px-2 rounded-full text-[10px] font-bold transition-colors']"
          >{{ tables.length }}</span>
        </button>
        <button
          @click="activeTab = 'metrics'"
          :class="[activeTab === 'metrics' ? 'border-amber-500 text-amber-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors duration-200 ease-in-out flex items-center gap-2']"
        >
          业务指标 (Metrics)
          <span 
             :class="[activeTab === 'metrics' ? 'bg-amber-100 text-amber-600' : 'bg-gray-100 text-gray-400', 'ml-1 py-0.5 px-2 rounded-full text-[10px] font-bold transition-colors']"
          >{{ dataset?.metric_count || 0 }}</span>
        </button>
        <button
          @click="activeTab = 'relationships'"
          :class="[activeTab === 'relationships' ? 'border-purple-500 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors duration-200 ease-in-out flex items-center gap-2']"
        >
          实体关系 (Relationships)
          <span 
             :class="[activeTab === 'relationships' ? 'bg-purple-100 text-purple-600' : 'bg-gray-100 text-gray-400', 'ml-1 py-0.5 px-2 rounded-full text-[10px] font-bold transition-colors']"
          >{{ dataset?.relationship_count || 0 }}</span>
        </button>
        <button
          @click="activeTab = 'visualization'"
          :class="[activeTab === 'visualization' ? 'border-indigo-500 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors duration-200 ease-in-out flex items-center gap-2']"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"/></svg>
          可视化 (Visual)
        </button>
        <button
          @click="activeTab = 'changelog'"
          :class="[activeTab === 'changelog' ? 'border-red-500 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors duration-200 ease-in-out flex items-center gap-2']"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          变更日志 (Changelog)
        </button>
      </nav>
    </div>

    <!-- Content: Tables -->
    <div v-show="activeTab === 'tables'" class="space-y-4">
      <div class="flex justify-between items-center gap-4 bg-white p-3 rounded-xl border border-gray-100 shadow-sm">
         <div class="relative flex-1 max-w-md">
            <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
               <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            </span>
            <input 
              v-model="searchQuery" 
              type="text" 
              class="block w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg leading-5 bg-gray-50 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary sm:text-sm transition-all focus:bg-white" 
              placeholder="搜索物理表名、业务名或描述..."
            >
         </div>
         <div class="flex items-center gap-4">
            <div class="text-xs text-gray-400 font-medium hidden sm:block">
               共找到 {{ filteredTables.length }} 张表
            </div>
            <button 
               v-has-perm="'element:metadata:edit_table'"
               @click="openCreateTableModal"
               class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-all shadow-md flex items-center gap-2 text-sm font-bold h-9"
            >
               <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
               新建数据表
            </button>
         </div>
      </div>

      <div v-if="loading" class="flex justify-center py-12">
        <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
      </div>
      
      <div v-else class="grid grid-cols-1 gap-4">
         <div v-if="filteredTables.length === 0" class="text-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
            <div class="text-4xl mb-3 grayscale opacity-30">🔍</div>
            <p class="text-gray-400 font-mono text-sm">未找到相关数据表。</p>
            <button v-if="searchQuery" @click="searchQuery = ''" class="mt-2 text-primary text-xs font-medium hover:underline">清除搜索</button>
            <button v-else @click="showImportModal = true" class="mt-4 text-primary text-sm font-medium hover:underline">立即导入</button>
         </div>
         
         <div v-for="table in filteredTables" :key="table.physical_name" class="bg-white rounded-xl border border-gray-100 p-5 hover:shadow-md transition-shadow group">
            <div class="flex justify-between items-start mb-4">
              <div class="flex-1">
                <div class="flex items-center gap-2">
                  <h3 class="font-bold text-gray-900 group-hover:text-primary transition-colors">{{ table.physical_name }}</h3>
                  <span class="text-[10px] font-bold text-gray-400 px-1.5 py-0.5 bg-gray-50 rounded border border-gray-100 uppercase tracking-wider">{{ table.term || '未命名' }}</span>
                </div>
                <p class="text-xs text-gray-500 mt-1 line-clamp-1">{{ table.description || '暂无描述' }}</p>
              </div>
              <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" v-if="hasPermission('element:metadata:edit_table')">
                <button 
                  @click.stop="openEditModal(table)"
                  class="text-gray-400 hover:text-primary transition-colors p-1.5 hover:bg-gray-100 rounded-md"
                  title="编辑表结构"
                >
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                </button>
                <button 
                  v-if="hasPermission('element:metadata:delete_table')"
                  @click.stop="handleDeleteTable(table.physical_name)"
                  class="text-gray-400 hover:text-red-500 transition-colors p-1.5 hover:bg-gray-100 rounded-md"
                  title="删除表"
                >
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
              </div>
            </div>
            
            <!-- Columns Minimal View -->
            <div class="flex flex-wrap gap-1.5">
              <div v-for="col in table.columns.slice(0, 10)" :key="col.physical_name" class="flex items-center gap-1 px-2 py-0.5 bg-slate-50 border border-slate-100 rounded-md text-[10px] font-mono group/col">
                <span class="text-amber-500" v-if="col.is_primary" title="Primary Key">🔑</span>
                <span class="text-slate-600 font-bold">{{ col.physical_name }}</span>
                <span class="text-slate-300">/</span>
                <span class="text-blue-500">{{ col.term || '?' }}</span>
              </div>
              <div v-if="table.columns.length > 10" class="px-2 py-0.5 bg-gray-50 border border-gray-100 rounded-md text-[10px] text-gray-400 font-medium">
                +{{ table.columns.length - 10 }} more columns
              </div>
            </div>
         </div>
      </div>
    </div>

    <!-- Content: Metrics -->
    <div v-if="activeTab === 'metrics'">
      <MetricList :dataset-id="datasetId" ref="metricListRef" @show-smart-discovery="showMetricModal = true" />
    </div>

    <!-- Content: Relationships -->
    <div v-if="activeTab === 'relationships'">
      <RelationshipList :dataset-id="datasetId" :tables="tables" />
    </div>

    <!-- Content: Visualization -->
    <div v-if="activeTab === 'visualization'">
      <SchemaGraph :dataset-id="datasetId" :tables="tables" />
    </div>

    <!-- Content: Changelog -->
    <div v-if="activeTab === 'changelog'">
      <ChangelogList :dataset-id="datasetId" />
    </div>

    <!-- Edit Table Modal -->
    <div v-if="showEditModal && editingTable" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showEditModal = false">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col max-h-[90vh] border border-gray-100">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-indigo-50 flex items-center justify-center border border-indigo-100">
                <svg class="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">编辑元数据定义</h2>
               <p class="text-xs text-gray-500 font-mono">{{ editingTable.physical_name }}</p>
             </div>
          </div>
          <button @click="showEditModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-8 space-y-8 bg-white">
           <!-- Table Level -->
           <div class="grid grid-cols-2 gap-6">
              <div class="space-y-2">
                 <label class="text-sm font-bold text-gray-700">业务名称 (Term)</label>
                 <input v-model="editingTable.term" class="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none" placeholder="例如：用户订单表">
              </div>
              <div class="space-y-2">
                 <label class="text-sm font-bold text-gray-700">描述 (Description)</label>
                 <input v-model="editingTable.description" class="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none" placeholder="简要描述表的用途...">
              </div>
           </div>

           <div class="border-t border-gray-100 pt-6">
              <div class="flex justify-between items-center mb-4">
                <h3 class="text-sm font-bold text-gray-900 flex items-center gap-2">
                   <span class="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                   字段定义 (Columns)
                </h3>
                <button 
                  @click="addColumn"
                  class="text-xs bg-indigo-50 text-indigo-600 px-3 py-1.5 rounded-lg border border-indigo-100 hover:bg-indigo-100 transition-colors flex items-center gap-1 font-bold"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                  添加字段
                </button>
              </div>
              
              <div class="space-y-3">
                 <!-- Header -->
                 <div class="grid grid-cols-12 gap-4 px-2 text-xs font-medium text-gray-500 uppercase">
                    <div class="col-span-3">Physical Name</div>
                    <div class="col-span-2">Type</div>
                    <div class="col-span-3">Business Term</div>
                    <div class="col-span-3">Description</div>
                    <div class="col-span-1"></div>
                 </div>

                 <!-- Rows -->
                 <div v-for="(col, index) in editingTable.columns" :key="index" class="grid grid-cols-12 gap-4 items-center p-2 bg-gray-50 rounded-lg border border-transparent hover:border-indigo-100 hover:bg-white hover:shadow-sm transition-all">
                    <div class="col-span-3">
                       <input v-model="col.physical_name" class="w-full bg-transparent border-b border-gray-200 focus:border-indigo-500 outline-none text-xs font-mono px-1 py-0.5" placeholder="物理名">
                    </div>
                    <div class="col-span-2">
                       <select v-model="col.type" class="w-full bg-transparent border-none text-[10px] text-gray-500 focus:ring-0 outline-none p-0">
                          <option value="String">String</option>
                          <option value="Int64">Int64</option>
                          <option value="Float64">Float64</option>
                          <option value="DateTime">DateTime</option>
                          <option value="Boolean">Boolean</option>
                          <option value="JSON">JSON</option>
                       </select>
                    </div>
                    <div class="col-span-3">
                       <input v-model="col.term" class="w-full bg-transparent border-b border-gray-300 focus:border-indigo-500 outline-none text-sm px-1 py-0.5" placeholder="字段业务名">
                    </div>
                    <div class="col-span-3">
                       <input v-model="col.description" class="w-full bg-transparent border-b border-gray-300 focus:border-indigo-500 outline-none text-xs text-gray-500 px-1 py-0.5" placeholder="描述...">
                    </div>
                    <div class="col-span-1 flex justify-end">
                       <button @click="removeColumn(index)" class="text-gray-300 hover:text-red-500 transition-colors">
                          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                       </button>
                    </div>
                 </div>
              </div>
           </div>
        </div>

        <!-- Footer -->
        <div class="p-6 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
           <button @click="showEditModal = false" class="px-5 py-2.5 bg-white border border-gray-200 rounded-xl text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors">取消</button>
           <button @click="handleUpdateTable" class="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-indigo-500/20 transition-all flex items-center gap-2">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
              保存修改
           </button>
        </div>
      </div>
    </div>

    <!-- Create Table Modal -->
    <div v-if="showCreateTableModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showCreateTableModal = false">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col max-h-[90vh] border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center border border-blue-100">
                <svg class="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">手动创建新表</h2>
               <p class="text-xs text-gray-500 font-medium">手动定义表结构、字段及其业务含义</p>
             </div>
          </div>
          <button @click="showCreateTableModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-8 space-y-8 bg-white">
           <div class="grid grid-cols-2 gap-6">
              <div class="space-y-2">
                 <label class="text-sm font-bold text-gray-700">物理名称 (Physical Name) *</label>
                 <input v-model="newTable.physical_name" class="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none font-mono" placeholder="例如：t_orders">
              </div>
              <div class="space-y-2">
                 <label class="text-sm font-bold text-gray-700">业务名称 (Term) *</label>
                 <input v-model="newTable.term" class="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" placeholder="例如：订单表">
              </div>
           </div>
           <div class="space-y-2">
              <label class="text-sm font-bold text-gray-700">描述 (Description)</label>
              <input v-model="newTable.description" class="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" placeholder="简要描述该表的作用...">
           </div>

           <div class="border-t border-gray-100 pt-6">
              <div class="flex justify-between items-center mb-4">
                <h3 class="text-sm font-bold text-gray-900 flex items-center gap-2">
                   <span class="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                   字段定义 (Columns)
                </h3>
                <button 
                  @click="newTable.columns.push({ physical_name: '', term: '', type: 'String', description: '' })"
                  class="text-xs bg-blue-50 text-blue-600 px-3 py-1.5 rounded-lg border border-blue-100 hover:bg-blue-100 transition-colors flex items-center gap-1 font-bold"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                  添加字段
                </button>
              </div>
              
              <div class="space-y-3">
                 <div class="grid grid-cols-12 gap-4 px-2 text-xs font-medium text-gray-500 uppercase">
                    <div class="col-span-3">Physical Name</div>
                    <div class="col-span-2">Type</div>
                    <div class="col-span-3">Business Term</div>
                    <div class="col-span-3">Description</div>
                    <div class="col-span-1"></div>
                 </div>

                 <div v-for="(col, index) in newTable.columns" :key="index" class="grid grid-cols-12 gap-4 items-center p-2 bg-gray-50 rounded-lg border border-transparent hover:border-blue-100 hover:bg-white transition-all">
                    <div class="col-span-3">
                       <input v-model="col.physical_name" class="w-full bg-transparent border-b border-gray-200 focus:border-blue-500 outline-none text-xs font-mono px-1 py-0.5" placeholder="字段名">
                    </div>
                    <div class="col-span-2">
                       <select v-model="col.type" class="w-full bg-transparent border-none text-[10px] text-gray-500 focus:ring-0 outline-none p-0">
                          <option value="String">String</option>
                          <option value="Int64">Int64</option>
                          <option value="Float64">Float64</option>
                          <option value="DateTime">DateTime</option>
                          <option value="Boolean">Boolean</option>
                          <option value="JSON">JSON</option>
                       </select>
                    </div>
                    <div class="col-span-3">
                       <input v-model="col.term" class="w-full bg-transparent border-b border-gray-300 focus:border-blue-500 outline-none text-sm px-1 py-0.5" placeholder="业务名">
                    </div>
                    <div class="col-span-3">
                       <input v-model="col.description" class="w-full bg-transparent border-b border-gray-300 focus:border-blue-500 outline-none text-xs text-gray-500 px-1 py-0.5" placeholder="描述...">
                    </div>
                    <div class="col-span-1 flex justify-end">
                       <button @click="newTable.columns.splice(index, 1)" class="text-gray-300 hover:text-red-500 transition-colors">
                          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                       </button>
                    </div>
                 </div>
              </div>
           </div>
        </div>

        <!-- Footer -->
        <div class="p-6 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
           <button @click="showCreateTableModal = false" class="px-5 py-2.5 bg-white border border-gray-200 rounded-xl text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors">取消</button>
           <button @click="handleCreateTable" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-blue-500/20 transition-all flex items-center gap-2">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
              立即创建
           </button>
        </div>
      </div>
    </div>

    <!-- Smart Import Wizard -->
    <SmartImportWizard 
      :show="showImportModal" 
      :dataset-id="datasetId"
      :dataset-display-name="dataset?.display_name"
      :dataset-physical-name="dataset?.name"
      :imported-table-names="tables.map((t) => t.physical_name)"
      @close="showImportModal = false"
      @saved="fetchDatasetInfo"
    />

    <SmartMetricModal
      :show="showMetricModal"
      :dataset-id="datasetId"
      @close="showMetricModal = false"
      @saved="() => metricListRef?.fetchMetrics()"
    />

    <!-- YAML Modal -->
    <div v-if="showYamlModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showYamlModal = false">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col max-h-[85vh] border border-gray-100">
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-purple-50 flex items-center justify-center border border-purple-100">
                <svg class="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">AI 元数据预览 (YAML)</h2>
               <p class="text-xs text-gray-400 font-medium">这是 Agent 在对话时实际获取到的语义上下文</p>
             </div>
          </div>
          <button @click="showYamlModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="flex-1 overflow-auto bg-slate-900 p-0">
          <pre class="p-6 text-sm font-mono text-cyan-400 leading-relaxed">{{ yamlContent }}</pre>
        </div>
        <div class="p-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
           <button @click="copyYaml" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-bold text-gray-700 hover:bg-gray-100 flex items-center gap-2 transition-all">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
              复制内容
           </button>
           <button @click="showYamlModal = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-bold text-gray-700 hover:bg-gray-100">关闭预览</button>
        </div>
      </div>
    </div>
    <!-- Delete Modal -->
    <div v-if="deleteTableId" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="deleteTableId = null">
       <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
          <div class="p-6 text-center">
             <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                <svg class="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
             </div>
             <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除表结构?</h3>
             <p class="text-sm text-gray-500 mb-6">
               您确定要删除表 <b>{{ deleteTableId }}</b> 吗？<br>此操作不可恢复。
             </p>
             <div class="flex gap-3 justify-center">
                <button @click="deleteTableId = null" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                <button @click="confirmDeleteTable" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30">确认删除</button>
             </div>
          </div>
       </div>
    </div>
    <!-- Dataset Description Modal -->
    <div v-if="showFullDescription" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showFullDescription = false">
       <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col border border-gray-100 animate-fade-in-up">
          <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
             <h3 class="text-lg font-black text-gray-900 flex items-center gap-2">
                <svg class="w-5 h-5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                数据集描述详情
             </h3>
             <button @click="showFullDescription = false" class="p-2 hover:bg-white rounded-full transition-colors group">
                <svg class="w-5 h-5 text-gray-400 group-hover:text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
             </button>
          </div>
          <div class="p-8 overflow-y-auto max-h-[60vh]">
             <div class="bg-blue-50/30 p-6 rounded-xl border border-blue-50">
                <p class="text-gray-700 text-base leading-relaxed whitespace-pre-wrap select-text">{{ dataset?.description }}</p>
             </div>
          </div>
          <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex justify-end">
             <button @click="showFullDescription = false" class="px-6 py-2 bg-slate-900 text-white rounded-lg hover:bg-black font-bold text-sm shadow-lg transition-all">
                关闭
             </button>
          </div>
       </div>
    </div>
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.animate-pulse-slow {
  animation: pulse-slow 4s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes pulse-slow {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.85; }
}
</style>
