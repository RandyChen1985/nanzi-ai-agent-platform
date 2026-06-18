<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { metadataApi } from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import TraceLogViewer from '../TraceLogViewer.vue'
import DatabaseImportModal from './DatabaseImportModal.vue'

const props = defineProps<{
  show: boolean,
  datasetId?: number,
  datasetDisplayName?: string,
  datasetPhysicalName?: string,
  importedTableNames?: string[],
}>()

const emit = defineEmits(['close', 'saved'])

const step = ref(1) // 1: Input, 2: Preview
const ddlText = ref('')
const analyzing = ref(false)
const saving = ref(false)

// Dataset Name State
const datasetName = ref('')
const datasetDisplayName = ref('')

// Toast
const { showToast } = useToast()

// Log Viewer State
const showLogs = ref(false)
const currentTraceId = ref('')
const showDbImportModal = ref(false)

// Progress State
const progress = ref(0)
const progressStatus = ref('')
let progressTimer: any = null
let statusTimer: any = null
let secondsTimer: any = null
const analysisSeconds = ref(0)

const startFakeProgress = () => {
  progress.value = 0
  analysisSeconds.value = 0
  progressStatus.value = '正在连接 AI 模型...'
  
  // Timer
  secondsTimer = setInterval(() => {
    analysisSeconds.value++
  }, 1000)
  
  // Progress Bar Animation (Logs 0 -> 90%)
  progressTimer = setInterval(() => {
    if (progress.value < 90) {
      // Slow down as it gets higher
      const inc = progress.value > 60 ? 1 : 5
      progress.value += inc
    }
  }, 500)

  // Status Message Rotation
  const messages = [
    '正在深度分析语义...',
    '正在识别表结构与字段...',
    '正在提取业务指标...',
    '正在构建实体关系图谱...',
    '正在进行数据一致性校验...'
  ]
  let msgIdx = 0
  statusTimer = setInterval(() => {
    if (msgIdx < messages.length) {
      progressStatus.value = messages[msgIdx] || ''
      msgIdx++
    }
  }, 2500)
}

const stopFakeProgress = () => {
  if (progressTimer) clearInterval(progressTimer)
  if (statusTimer) clearInterval(statusTimer)
  if (secondsTimer) clearInterval(secondsTimer)
  progressTimer = null
  statusTimer = null
  secondsTimer = null
}

onUnmounted(() => {
  stopFakeProgress()
})

// Preview Data
const previewData = ref<{
  tables: any[],
  metrics: any[],
  relationships: any[]
}>({
  tables: [],
  metrics: [],
  relationships: []
})

const handleAnalyze = async () => {
  if (!ddlText.value.trim()) return
  
  analyzing.value = true
  currentTraceId.value = '' // Reset trace ID
  startFakeProgress()
  
  try {
    const res = await metadataApi.analyzeDDL(ddlText.value)
    
    // Capture Trace ID on success (if available)
    if (res.data?.data?._trace_id) {
       currentTraceId.value = res.data.data._trace_id
    }
    
    // Finish progress
    progress.value = 100
    progressStatus.value = '分析完成！'
    
    // Slight delay to let user see 100%
    setTimeout(() => {
      // Support both single table dict or new ImportResult structure
      const data = res.data.data
      
      let tables: any[] = []
      let metrics: any[] = []
      let relationships: any[] = []

      if (data.tables) {
         // New structure
         tables = data.tables || []
         metrics = data.metrics || []
         relationships = data.relationships || []
      } else {
         // Fallback for single table legacy
         tables = [data]
         metrics = (data.metrics || []).map((m: any) => ({
            ...m,
            calculation_logic: m.calculation || m.calculation_logic || ''
         }))
         relationships = []
      }

      // Ensure all columns have a standardized type
      tables.forEach((t: any) => {
        if (t.columns) {
          t.columns.forEach((c: any) => {
            c.type = normalizeType(c.type)
          })
        }
      })

      previewData.value = {
        tables,
        metrics,
        relationships
      }
      
      // Auto-generate dataset name and display name
      if (previewData.value.tables.length > 0) {
          const firstTable = previewData.value.tables[0]
          datasetName.value = firstTable.physical_name + '_ds'
          datasetDisplayName.value = (firstTable.term || firstTable.physical_name) + '数据集'
      } else {
          datasetName.value = `import_ds_${new Date().toISOString().slice(0,10).replace(/-/g,'')}`
          datasetDisplayName.value = '新数据集_' + new Date().toISOString().slice(0,10)
      }
      
      step.value = 2
      stopFakeProgress()
      analyzing.value = false
      showToast('智能分析完成', 'success')
    }, 500)

  } catch (e: any) {
    stopFakeProgress()
    analyzing.value = false
    console.error('Analysis failed', e)
    
    // Parse Trace ID from error detail
    const detail = e.response?.data?.detail || ''
    const match = detail.match(/Trace ID: ([a-zA-Z0-9-]+)/)
    if (match) {
       currentTraceId.value = match[1]
    }
    
    if (e.code === 'ECONNABORTED' || e.message?.includes('timeout')) {
       showToast('分析超时，请尝试减少输入内容或稍后重试', 'error', 5000)
    } else {
       showToast('智能分析失败，请检查输入内容或重试', 'error')
    }
  }
}

const handleSave = async () => {
  saving.value = true
  try {
    if (previewData.value.tables.length === 0) return
    
    let targetDatasetId = props.datasetId
    
    // 1. Create Dataset if not in append mode
    if (!targetDatasetId) {
      if (!datasetName.value.trim() || !datasetDisplayName.value.trim()) {
        showToast('请输入目标数据集 ID 和名称', 'warning')
        saving.value = false
        return
      }

      const dsRes = await metadataApi.createDataset({
        name: datasetName.value.trim(),
        display_name: datasetDisplayName.value.trim(),
        description: 'Imported via Smart Wizard',
        tags: ['auto-import']
      })
      targetDatasetId = (dsRes.data as any).id
    }
    
    // 2. Save Tables
    const tableMap = new Map<string, number>() // physical_name -> id
    
    for (const table of previewData.value.tables) {
       const res = await metadataApi.saveTable(targetDatasetId as number, table)
       tableMap.set(table.physical_name, (res.data as any).id)
    }
    
    // 3. Save Metrics
    for (const metric of previewData.value.metrics) {
       await metadataApi.createMetric(targetDatasetId as number, metric)
    }
    
    // 4. Save Relationships
    for (const rel of previewData.value.relationships) {
       // Resolve Table IDs
       const sourceId = tableMap.get(rel.source_table)
       const targetId = tableMap.get(rel.target_table)
       
       if (sourceId && targetId) {
          await metadataApi.createRelationship(targetDatasetId as number, {
             ...rel,
             source_table_id: sourceId,
             target_table_id: targetId,
             join_condition: rel.condition || '1=1', // Fallback
             join_type: rel.type || 'left'
          })
       } else {
          console.warn(`Skipping relationship ${rel.source_table}->${rel.target_table} because tables not found in this import batch.`)
       }
    }
    
    emit('saved')
    showToast(props.datasetId ? '追加元数据成功' : '保存成功', 'success')
    handleClose()
    
  } catch (e: any) {
    console.error('Import failed', e)
    if (e.response?.status === 400 && e.response?.data?.detail?.includes('存在')) {
       showToast('数据集名称已存在，请修改名称后重试', 'error', 4000)
    } else {
       showToast(e.response?.data?.detail || '保存失败，请重试', 'error')
    }
  } finally {
    saving.value = false
  }
}

const handleClose = () => {
  if (analyzing.value) return // Prevent closing while analyzing
  step.value = 1
  ddlText.value = ''
  previewData.value = { tables: [], metrics: [], relationships: [] }
  stopFakeProgress()
  emit('close')
}

const handleDbDdlConfirm = (ddl: string) => {
  if (ddlText.value.trim()) {
    ddlText.value += '\n\n' + ddl
  } else {
    ddlText.value = ddl
  }
}

// Helpers
const removeTable = (idx: number) => previewData.value.tables.splice(idx, 1)
const removeMetric = (idx: number) => previewData.value.metrics.splice(idx, 1)
const removeRel = (idx: number) => previewData.value.relationships.splice(idx, 1)

const normalizeType = (rawType: string): string => {
  if (!rawType) return 'String'
  const type = rawType.toLowerCase().trim()
  
  if (type.includes('int') || type.includes('long') || type.includes('number')) {
    return 'Int64'
  }
  if (type.includes('float') || type.includes('double') || type.includes('decimal') || type.includes('numeric')) {
    return 'Float64'
  }
  if (type.includes('date') || type.includes('time')) {
    return 'DateTime'
  }
  if (type.includes('bool')) {
    return 'Boolean'
  }
  if (type.includes('json') || type.includes('object') || type.includes('map')) {
    return 'JSON'
  }
  return 'String'
}

</script>

<template>
  <div v-if="show" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
    <div class="bg-white rounded-xl shadow-2xl w-full max-w-6xl h-[90vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
      
      <!-- Header -->
      <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-blue-50 to-indigo-50">
        <div class="flex items-center gap-4">
           <div class="w-12 h-12 rounded-xl bg-white shadow-sm flex items-center justify-center text-blue-600 border border-blue-100">
              <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/></svg>
           </div>
           <div>
             <h2 class="text-xl font-bold text-gray-900">智能元数据导入向导</h2>
             <div class="flex items-center gap-3 mt-1">
                <!-- Step 1 -->
                <div class="flex items-center gap-2" :class="step >= 1 ? 'text-blue-600' : 'text-gray-400'">
                  <div class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors"
                       :class="step >= 1 ? 'border-current bg-blue-50' : 'border-gray-200'">
                     1
                  </div>
                  <span class="text-sm font-medium">输入定义</span>
                </div>
                
                <!-- Arrow/Line -->
                <div class="w-8 h-0.5 bg-gray-200 relative">
                   <div class="absolute inset-y-0 left-0 bg-blue-600 transition-all duration-300" :style="{ width: step >= 2 ? '100%' : '0%' }"></div>
                </div>
                
                <!-- Step 2 -->
                <div class="flex items-center gap-2" :class="step >= 2 ? 'text-blue-600' : 'text-gray-400'">
                  <div class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors"
                       :class="step >= 2 ? 'border-current bg-blue-50' : 'border-gray-200'">
                     2
                  </div>
                  <span class="text-sm font-medium">预览确认</span>
                </div>
             </div>
           </div>
        </div>
        <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors p-2 hover:bg-white rounded-full">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Content Step 1: Input -->
      <div v-if="step === 1" class="flex-1 p-8 flex flex-col gap-6 overflow-hidden">
        <div class="flex-1 relative">
           <textarea 
             v-model="ddlText" 
             :disabled="analyzing"
             class="w-full h-full bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 p-6 font-mono text-sm text-gray-800 resize-none shadow-inner disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed transition-colors"
             placeholder="请在此粘贴 SQL DDL (CREATE TABLE...) 或者业务需求文档...&#10;&#10;示例:&#10;CREATE TABLE orders (id int, user_id int); -- 订单表&#10;CREATE TABLE users (id int, name varchar); -- 用户表&#10;-- 用户表和订单表是一对多关系"
           ></textarea>
            <div class="absolute top-4 right-4 z-10">
               <button 
                 @click="showDbImportModal = true"
                 :disabled="analyzing"
                 class="bg-white/80 backdrop-blur border border-gray-200 text-blue-600 hover:text-blue-700 hover:bg-white px-3 py-1.5 rounded-lg text-xs font-bold shadow-sm flex items-center gap-1.5 transition-all disabled:opacity-50"
               >
                 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
                 从数据库加载 (MySQL/CK)
               </button>
            </div>
            <div class="absolute bottom-4 right-4 text-xs text-gray-400 flex items-center gap-4">
              <span class="font-mono">{{ ddlText.length }} 字符</span>
              <span>支持 SQL, Markdown, 自然语言</span>
            </div>
        </div>
        <div class="flex justify-between items-center">
           <!-- Log Button -->
           <button 
             v-if="currentTraceId"
             @click="showLogs = true"
             class="text-xs text-gray-400 hover:text-blue-600 transition-colors flex items-center gap-1.5 px-3 py-2 rounded hover:bg-gray-100"
             title="查看执行过程日志"
           >
             <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
             <span>查看调试日志</span>
           </button>
           <div v-else></div>

           <button              @click="handleAnalyze"
              :disabled="analyzing || !ddlText"
              class="px-8 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-bold shadow-lg shadow-blue-500/30 transition-all flex items-center gap-3 disabled:opacity-50 disabled:cursor-not-allowed relative overflow-hidden"
            >
              <!-- Progress Background -->
              <div 
                v-if="analyzing" 
                class="absolute left-0 top-0 bottom-0 bg-blue-800/50 transition-all duration-300 ease-linear"
                :style="{ width: `${progress}%` }"
              ></div>
              
              <!-- Content -->
              <div class="relative flex items-center gap-2 z-10 w-full justify-center min-w-[160px]">
                 <svg v-if="analyzing" class="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                 <span v-if="analyzing" class="text-sm font-light tracking-wide">{{ progressStatus }} ({{ analysisSeconds }}s)</span>
                 <span v-else>开始智能识别</span>
              </div>
            </button>
        </div>
      </div>

      <!-- Content Step 2: Preview -->
      <div v-else class="flex-1 flex overflow-hidden">
         <!-- Sidebar Navigation -->
         <div class="w-64 bg-gray-50 border-r border-gray-100 flex flex-col py-6">
            
            <!-- Dataset Name Input (Only if not in append mode) -->
            <div class="px-6 mb-6" v-if="!datasetId">
               <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">数据集 ID (Unique)</label>
               <div class="relative mb-3">
                 <input 
                   v-model="datasetName" 
                   class="w-full pl-3 pr-8 py-2 bg-white border border-gray-200 rounded-lg text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent placeholder-gray-400 font-mono"
                   placeholder="e.g. user_orders_ds"
                 >
                 <div class="absolute right-2 top-2.5 text-gray-400 pointer-events-none">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg>
                 </div>
               </div>
               
               <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">显示名称</label>
               <div class="relative">
                 <input 
                   v-model="datasetDisplayName" 
                   class="w-full pl-3 pr-8 py-2 bg-white border border-gray-200 rounded-lg text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent placeholder-gray-400"
                   placeholder="e.g. 用户订单数据集"
                 >
                 <div class="absolute right-2 top-2.5 text-gray-400 pointer-events-none">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                 </div>
               </div>
               <p class="text-[10px] text-gray-400 mt-2 leading-tight">数据集 ID 全局唯一，用于标识；显示名称用于 UI 展示。</p>
            </div>

            <!-- Append Mode Indicator -->
            <div class="px-6 mb-6" v-else>
               <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">导入模式</label>
               <div class="flex items-center gap-2 p-3 bg-blue-50 border border-blue-100 rounded-xl">
                  <div class="w-8 h-8 rounded-lg bg-white flex items-center justify-center text-blue-600 shadow-sm">
                     <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                  </div>
                  <div class="min-w-0">
                     <div class="text-[10px] font-bold text-blue-800 uppercase tracking-wider">追加到现有数据集</div>
                     <div class="text-xs font-bold text-blue-700 truncate" :title="datasetDisplayName">{{ datasetDisplayName || '当前数据集' }}</div>
                     <div class="text-[9px] text-blue-400 font-mono flex items-center gap-1 mt-0.5">
                        <span class="bg-blue-100 px-1 rounded">#{{ datasetPhysicalName }}</span>
                        <span>ID: {{ datasetId }}</span>
                     </div>
                  </div>
               </div>
            </div>

            <div class="px-6 mb-4 font-bold text-xs text-gray-400 uppercase tracking-wider">识别结果概览</div>
            
            <div class="flex-1 overflow-y-auto space-y-1 px-4">
               <div class="flex justify-between items-center p-2 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-sm font-bold text-gray-700">Tables</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.tables.length }}</span>
               </div>
               <div class="flex justify-between items-center p-2 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-sm font-bold text-gray-700">Metrics</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.metrics.length }}</span>
               </div>
               <div class="flex justify-between items-center p-2 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-sm font-bold text-gray-700">Relationships</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.relationships.length }}</span>
               </div>
            </div>

            <div class="px-6 mt-4">
               <button @click="step = 1" class="w-full py-2 border border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-100">
                  ← 返回修改输入
               </button>
            </div>
         </div>

         <!-- Main Preview Area -->
         <div class="flex-1 overflow-y-auto bg-gray-50/50 p-8 space-y-8">
            
            <!-- Tables Section -->
            <section v-if="previewData.tables.length > 0">
               <h3 class="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                 <span class="w-2 h-6 bg-blue-500 rounded-full"></span>
                 识别到的表结构
               </h3>
               <div class="grid gap-4">
                  <div v-for="(table, idx) in previewData.tables" :key="idx" class="bg-white rounded-xl border border-gray-200 p-5 shadow-sm group">
                     <div class="flex justify-between items-center mb-3">
                        <div class="flex items-center gap-3">
                           <div class="px-2 py-1 bg-blue-50 text-blue-700 font-mono text-xs rounded border border-blue-100">{{ table.physical_name }}</div>
                           <input v-model="table.term" class="text-sm font-bold border-none focus:ring-0 p-0 text-gray-800" placeholder="业务名称">
                        </div>
                        <button @click="removeTable(idx)" class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                           <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                        </button>
                     </div>
                     <p class="text-xs text-gray-500 mb-3">{{ table.description || '无描述' }}</p>
                     
                     <!-- Columns Preview (Editable) -->
                     <div class="space-y-2">
                        <div class="grid grid-cols-12 gap-2 px-2 text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                           <div class="col-span-3">物理字段名</div>
                           <div class="col-span-2">类型</div>
                           <div class="col-span-3">业务术语 (Term)</div>
                           <div class="col-span-3">描述</div>
                           <div class="col-span-1"></div>
                        </div>
                        <div v-for="(col, cIdx) in table.columns" :key="cIdx" class="grid grid-cols-12 gap-2 items-center bg-gray-50/50 p-1.5 rounded-lg border border-transparent hover:border-blue-100 hover:bg-white transition-all group/col">
                           <div class="col-span-3">
                              <input v-model="col.physical_name" class="w-full bg-transparent border-none focus:ring-0 p-0 text-xs font-mono text-gray-600" placeholder="物理名">
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
                              <input v-model="col.term" class="w-full bg-transparent border-none focus:ring-0 p-0 text-xs text-gray-800 font-medium" placeholder="业务名">
                           </div>
                           <div class="col-span-3">
                              <input v-model="col.description" class="w-full bg-transparent border-none focus:ring-0 p-0 text-xs text-gray-500" placeholder="描述...">
                           </div>
                           <div class="col-span-1 flex justify-end">
                              <button @click="table.columns.splice(cIdx, 1)" class="text-gray-300 hover:text-red-500 opacity-0 group-hover/col:opacity-100 transition-opacity">
                                 <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                              </button>
                           </div>
                        </div>
                        
                        <!-- Add Column Helper (Optional) -->
                        <button 
                           @click="table.columns.push({ physical_name: '', term: '', type: 'String', description: '' })"
                           class="w-full py-1.5 mt-1 border border-dashed border-gray-200 rounded-lg text-[10px] text-gray-400 hover:text-blue-500 hover:border-blue-200 hover:bg-blue-50 transition-all flex items-center justify-center gap-1"
                        >
                           <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                           添加更多字段
                        </button>
                     </div>
                  </div>
               </div>
            </section>

            <!-- Metrics Section -->
            <section v-if="previewData.metrics.length > 0">
               <h3 class="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                 <span class="w-2 h-6 bg-amber-500 rounded-full"></span>
                 业务指标 (Metrics)
               </h3>
               <div class="grid grid-cols-2 gap-4">
                  <div v-for="(metric, idx) in previewData.metrics" :key="idx" class="bg-white rounded-xl border border-gray-200 p-5 shadow-sm group border-l-4 border-l-amber-400">
                     <div class="flex justify-between items-start">
                        <div>
                           <div class="flex items-center gap-2 mb-1">
                              <span class="font-bold text-gray-900">{{ metric.display_name }}</span>
                              <span class="text-[10px] font-mono text-gray-400">({{ metric.name }})</span>
                           </div>
                           <p class="text-xs text-gray-500 line-clamp-2 h-8">{{ metric.description }}</p>
                        </div>
                        <button @click="removeMetric(idx)" class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                           <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                        </button>
                     </div>
                     <div class="mt-3 bg-amber-50 p-2 rounded border border-amber-100">
                        <code class="text-[10px] text-amber-700 break-all">{{ metric.calculation_logic }}</code>
                     </div>
                  </div>
               </div>
            </section>

            <!-- Relationships Section -->
            <section v-if="previewData.relationships.length > 0">
               <h3 class="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                 <span class="w-2 h-6 bg-purple-500 rounded-full"></span>
                 实体关系 (Relationships)
               </h3>
               <div class="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
                  <table class="min-w-full text-sm">
                     <thead class="bg-gray-50 text-gray-500 font-medium">
                        <tr>
                           <th class="px-4 py-3 text-left">Source</th>
                           <th class="px-4 py-3 text-center">Type</th>
                           <th class="px-4 py-3 text-left">Target</th>
                           <th class="px-4 py-3 text-left">Condition</th>
                           <th class="px-4 py-3"></th>
                        </tr>
                     </thead>
                     <tbody class="divide-y divide-gray-100">
                        <tr v-for="(rel, idx) in previewData.relationships" :key="idx" class="group hover:bg-gray-50">
                           <td class="px-4 py-3 font-mono text-blue-600 font-bold">{{ rel.source_table }}</td>
                           <td class="px-4 py-3 text-center">
                              <span class="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs border border-purple-100">{{ rel.type }}</span>
                           </td>
                           <td class="px-4 py-3 font-mono text-blue-600 font-bold">{{ rel.target_table }}</td>
                           <td class="px-4 py-3 font-mono text-xs text-gray-500">{{ rel.condition }}</td>
                           <td class="px-4 py-3 text-right">
                              <button @click="removeRel(idx)" class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                                 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                              </button>
                           </td>
                        </tr>
                     </tbody>
                  </table>
               </div>
            </section>

            <!-- Empty State -->
            <div v-if="previewData.tables.length === 0 && previewData.metrics.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-400">
               <svg class="w-16 h-16 mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
               <p>似乎没有识别出有效内容，请检查输入或重试。</p>
            </div>
         </div>
      </div>

      <!-- Footer Step 2 -->
      <div v-if="step === 2" class="p-6 border-t border-gray-100 bg-white flex justify-end gap-3">
         <button @click="handleClose" class="px-6 py-2 border border-gray-300 rounded-xl text-gray-700 font-medium hover:bg-gray-50">取消</button>
         <button 
            @click="handleSave"
            :disabled="saving || previewData.tables.length === 0"
            class="px-8 py-2 bg-green-600 hover:bg-green-700 text-white rounded-xl font-bold shadow-lg shadow-green-500/20 transition-all flex items-center gap-2 disabled:opacity-50"
         >
            <svg v-if="saving" class="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            {{ saving ? '正在入库...' : '确认并保存' }}
         </button>
      </div>

    </div>
  </div>
  <!-- Log Viewer Modal -->
  <TraceLogViewer 
    :visible="showLogs" 
    :trace-id="currentTraceId" 
    @close="showLogs = false" 
  />
  <DatabaseImportModal 
    :show="showDbImportModal"
    :imported-table-names="importedTableNames || []"
    @close="showDbImportModal = false"
    @confirm="handleDbDdlConfirm"
  />
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
