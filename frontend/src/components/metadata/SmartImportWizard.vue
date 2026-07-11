<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import axios from '../../utils/axios'
import { metadataApi } from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import TraceLogViewer from '../TraceLogViewer.vue'
import DatabaseImportModal from './DatabaseImportModal.vue'

const props = defineProps<{
  show: boolean,
  datasetId?: number,
  datasetDisplayName?: string,
  datasetPhysicalName?: string,
  datasetDataSource?: string,
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
const importDataSourceName = ref('')
const defaultDataSource = ref('')

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

const fetchSystemDefaultDataSource = async () => {
  try {
    const res = await axios.get('/api/portal/system/configs')
    const groups = res.data
    if (groups?.data_api) {
      const config = groups.data_api.find((item: any) => item.key === 'external_sql_data_source')
      if (config?.value) {
        defaultDataSource.value = config.value
      }
    }
  } catch {
    // 加载失败时使用后端默认值
  }
}

watch(() => props.show, (visible) => {
  if (visible && !props.datasetId) {
    fetchSystemDefaultDataSource()
  }
})

onMounted(() => {
  if (!props.datasetId) {
    fetchSystemDefaultDataSource()
  }
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

const expandedPreviewTables = ref<Record<number, boolean>>({})

const togglePreviewTableExpand = (idx: number) => {
  expandedPreviewTables.value[idx] = !expandedPreviewTables.value[idx]
}

const getDescriptionRows = (desc?: string) => {
  if (!desc?.trim()) return 2
  const lineCount = desc.split('\n').length
  const wrappedLines = Math.ceil(desc.length / 46)
  return Math.min(12, Math.max(2, Math.max(lineCount, wrappedLines)))
}

const EDITABLE_INPUT =
  'w-full px-2.5 py-1.5 bg-amber-50/60 border-2 border-dashed border-amber-300 rounded-lg text-sm text-gray-800 shadow-sm hover:border-amber-400 hover:bg-amber-50 focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-500 focus:bg-white transition-colors'

const EDITABLE_INPUT_SM =
  'w-full px-2.5 py-1.5 bg-amber-50/60 border border-dashed border-amber-300 rounded-md text-xs text-gray-800 hover:border-amber-400 hover:bg-amber-50 focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-500 focus:bg-white transition-colors'

const EDITABLE_TEXTAREA =
  'w-full px-3 py-2 bg-amber-50/60 border-2 border-dashed border-amber-300 rounded-lg text-xs text-gray-700 leading-relaxed shadow-sm hover:border-amber-400 hover:bg-amber-50 focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-500 focus:bg-white transition-colors resize-y min-h-[3rem]'

const EDITABLE_CELL =
  'w-full px-2 py-1.5 bg-amber-50/50 border border-dashed border-amber-300 rounded-md hover:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-500 focus:bg-white transition-colors'

const EDITABLE_SELECT =
  'w-full px-2 py-1.5 bg-amber-50/50 border border-dashed border-amber-300 rounded-md text-[10px] text-gray-700 hover:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-500 focus:bg-white transition-colors'

const expandAllPreviewTables = (tableCount: number) => {
  const expanded: Record<number, boolean> = {}
  for (let i = 0; i < tableCount; i++) {
    expanded[i] = true
  }
  expandedPreviewTables.value = expanded
}

const applyImportPreview = (data: any) => {
  let tables: any[] = []
  let metrics: any[] = []
  let relationships: any[] = []

  if (data.tables) {
    tables = data.tables || []
    metrics = data.metrics || []
    relationships = data.relationships || []
  } else {
    tables = [data]
    metrics = (data.metrics || []).map((m: any) => ({
      ...m,
      calculation_logic: m.calculation || m.calculation_logic || '',
    }))
    relationships = []
  }

  tables.forEach((t: any) => {
    if (t.columns) {
      t.columns.forEach((c: any) => {
        c.type = normalizeType(c.type)
      })
    }
  })

  previewData.value = { tables, metrics, relationships }
  expandAllPreviewTables(tables.length)

  if (previewData.value.tables.length > 0) {
    const firstTable = previewData.value.tables[0]
    datasetName.value = firstTable.physical_name + '_ds'
    datasetDisplayName.value = (firstTable.term || firstTable.physical_name) + '数据集'
  } else {
    datasetName.value = `import_ds_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`
    datasetDisplayName.value = '新数据集_' + new Date().toISOString().slice(0, 10)
  }

  step.value = 2
}

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
      applyImportPreview(res.data.data)
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

      const dataSource = importDataSourceName.value || defaultDataSource.value || undefined
      const dsRes = await metadataApi.createDataset({
        name: datasetName.value.trim(),
        display_name: datasetDisplayName.value.trim(),
        description: 'Imported via Smart Wizard',
        tags: ['auto-import'],
        ...(dataSource ? { data_source: dataSource } : {}),
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
  expandedPreviewTables.value = {}
  importDataSourceName.value = ''
  stopFakeProgress()
  emit('close')
}

const handleDbDdlConfirm = (payload: { ddl: string; dataSourceName?: string }) => {
  if (payload.dataSourceName) {
    importDataSourceName.value = payload.dataSourceName
  }
  if (ddlText.value.trim()) {
    ddlText.value += '\n\n' + payload.ddl
  } else {
    ddlText.value = payload.ddl
  }
}

const handleProfileImportConfirm = (payload: { preview: any; dataSourceName?: string }) => {
  if (payload.dataSourceName) {
    importDataSourceName.value = payload.dataSourceName
  }
  applyImportPreview(payload.preview)
  showToast('已使用摸排画像直接进入预览，无需重复分析', 'success')
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
  <div v-if="show" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
    <div class="bg-white rounded-xl shadow-2xl w-full max-w-5xl h-[92vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
      
      <!-- Header -->
      <div
        class="border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-blue-50 to-indigo-50 shrink-0 px-5 py-3"
      >
        <div class="flex items-center gap-3">
           <div
             class="rounded-xl bg-white shadow-sm flex items-center justify-center text-blue-600 border border-blue-100 shrink-0 w-9 h-9"
           >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/></svg>
           </div>
           <div>
             <h2 class="font-bold text-gray-900 text-lg">智能元数据导入向导</h2>
             <div class="flex items-center gap-2 mt-0.5">
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
        <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors p-2 hover:bg-white rounded-full shrink-0">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Content Step 1: Input -->
      <div v-if="step === 1" class="flex-1 flex flex-col min-h-0 overflow-hidden">
        <div class="flex-1 p-5 min-h-0 overflow-hidden">
        <div class="relative h-full">
           <textarea 
             v-model="ddlText" 
             :disabled="analyzing"
             class="w-full h-full bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 p-5 font-mono text-sm text-gray-800 resize-none shadow-inner disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed transition-colors"
             placeholder="请在此粘贴 SQL DDL (CREATE TABLE...) 或者业务需求文档...&#10;&#10;示例:&#10;CREATE TABLE orders (id int, user_id int); -- 订单表&#10;CREATE TABLE users (id int, name varchar); -- 用户表&#10;-- 用户表和订单表是一对多关系"
           ></textarea>
            <div class="absolute top-3 right-3 z-10">
               <button 
                 @click="showDbImportModal = true"
                 :disabled="analyzing"
                 class="bg-white/80 backdrop-blur border border-gray-200 text-blue-600 hover:text-blue-700 hover:bg-white px-3 py-1.5 rounded-lg text-xs font-bold shadow-sm flex items-center gap-1.5 transition-all disabled:opacity-50"
               >
                 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
                 从数据库加载 (MySQL/CK)
               </button>
            </div>
            <div class="absolute bottom-3 right-3 text-xs text-gray-400 flex items-center gap-4">
              <span class="font-mono">{{ ddlText.length }} 字符</span>
              <span>支持 SQL, Markdown, 自然语言</span>
            </div>
        </div>
        </div>

        <!-- Footer Step 1 -->
        <div class="px-5 py-2.5 border-t border-gray-100 bg-white flex justify-between items-center shrink-0">
           <button 
             v-if="currentTraceId"
             @click="showLogs = true"
             class="text-xs text-gray-400 hover:text-blue-600 transition-colors flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100"
             title="查看执行过程日志"
           >
             <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
             <span>查看调试日志</span>
           </button>
           <div v-else></div>

           <button
              @click="handleAnalyze"
              :disabled="analyzing || !ddlText"
              class="px-5 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-bold shadow-sm transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed relative overflow-hidden min-w-[140px] justify-center"
            >
              <div 
                v-if="analyzing" 
                class="absolute left-0 top-0 bottom-0 bg-blue-800/50 transition-all duration-300 ease-linear"
                :style="{ width: `${progress}%` }"
              ></div>
              <div class="relative flex items-center gap-2 z-10">
                 <svg v-if="analyzing" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                 <span v-if="analyzing" class="text-sm font-light tracking-wide">{{ progressStatus }} ({{ analysisSeconds }}s)</span>
                 <span v-else>开始智能识别</span>
              </div>
            </button>
        </div>
      </div>

      <!-- Content Step 2: Preview -->
      <div v-else class="flex-1 flex min-h-0 overflow-hidden">
         <!-- Sidebar Navigation -->
         <div class="w-60 shrink-0 bg-gray-50 border-r border-gray-100 flex flex-col min-h-0 overflow-hidden">
            <div class="flex-1 min-h-0 overflow-y-auto py-4">
            <!-- 数据集信息 -->
            <div class="px-4 mb-4 space-y-3">
              <div v-if="!datasetId" class="space-y-3">
                <div class="flex items-center gap-1.5 text-[10px] text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5">
                  <svg class="w-3 h-3 shrink-0 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                  <span><strong class="font-bold">虚线框</strong>可编辑</span>
                </div>
                <div>
                  <label class="flex items-center gap-1 text-[10px] font-bold text-gray-500 uppercase tracking-wider mb-1">
                    <span>数据集 ID</span>
                    <span class="text-amber-600 normal-case tracking-normal font-semibold">可编辑</span>
                  </label>
                  <input
                    v-model="datasetName"
                    :class="[EDITABLE_INPUT, 'font-mono text-xs']"
                    placeholder="e.g. user_orders_ds"
                  >
                </div>
                <div>
                  <label class="flex items-center gap-1 text-[10px] font-bold text-gray-500 uppercase tracking-wider mb-1">
                    <span>显示名称</span>
                    <span class="text-amber-600 normal-case tracking-normal font-semibold">可编辑</span>
                  </label>
                  <input
                    v-model="datasetDisplayName"
                    :class="[EDITABLE_INPUT, 'text-xs']"
                    placeholder="e.g. 用户订单数据集"
                  >
                </div>
              </div>
              <div v-else class="p-2.5 bg-blue-50 border border-blue-100 rounded-lg space-y-1">
                <div class="text-[10px] font-bold text-blue-800 uppercase tracking-wider">追加到现有数据集</div>
                <div class="text-xs font-bold text-blue-700 break-words" :title="datasetDisplayName">{{ datasetDisplayName || '当前数据集' }}</div>
                <div class="text-[10px] text-blue-500 font-mono flex flex-col gap-0.5">
                  <span class="bg-blue-100 px-1.5 py-0.5 rounded w-fit">#{{ datasetPhysicalName }}</span>
                  <span>ID: {{ datasetId }}</span>
                </div>
              </div>
            </div>

            <div
              v-if="(!datasetId && importDataSourceName) || (datasetId && datasetDataSource)"
              class="px-4 mb-4"
            >
              <label class="block text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">数据源 ID</label>
              <div class="px-2 py-1.5 rounded-lg bg-emerald-50 border border-emerald-100 text-[11px] text-emerald-700">
                <span class="font-mono font-bold break-all">{{ datasetId ? datasetDataSource : importDataSourceName }}</span>
              </div>
            </div>

            <div class="px-4 mb-2 font-bold text-[10px] text-gray-400 uppercase tracking-wider">识别结果概览</div>
            
            <div class="space-y-1 px-3 pb-2">
               <div class="flex justify-between items-center px-2 py-1.5 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-xs font-bold text-gray-700">Tables</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.tables.length }}</span>
               </div>
               <div class="flex justify-between items-center px-2 py-1.5 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-xs font-bold text-gray-700">Metrics</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.metrics.length }}</span>
               </div>
               <div class="flex justify-between items-center px-2 py-1.5 rounded-lg bg-white border border-gray-200 shadow-sm">
                  <span class="text-xs font-bold text-gray-700">Relationships</span>
                  <span class="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{{ previewData.relationships.length }}</span>
               </div>
            </div>

            </div>

            <div class="shrink-0 px-4 py-2.5 border-t border-gray-100 bg-gray-50">
               <button @click="step = 1" class="w-full py-1.5 border border-gray-300 rounded-lg text-xs text-gray-600 hover:bg-gray-100">
                  ← 返回修改输入
               </button>
            </div>
         </div>

         <!-- Main Preview Area -->
         <div class="flex-1 min-h-0 overflow-y-auto bg-gray-50/50 p-4 md:p-5 space-y-5">
            
            <!-- Tables Section -->
            <section v-if="previewData.tables.length > 0">
               <h3 class="text-base font-bold text-gray-900 flex items-center gap-2">
                 <span class="w-1.5 h-5 bg-blue-500 rounded-full"></span>
                 识别到的表结构
                 <span class="text-[11px] font-normal text-gray-400">物理表名只读，虚线框可编辑</span>
               </h3>
               <div class="grid gap-3 mt-2">
                  <div
                    v-for="(table, idx) in previewData.tables"
                    :key="idx"
                    class="bg-white rounded-xl border border-gray-200 shadow-sm group overflow-hidden"
                  >
                     <div class="p-4">
                        <div class="flex justify-between items-start gap-2 mb-2">
                           <div class="min-w-0 flex-1">
                              <div class="flex items-center gap-2 flex-wrap mb-2">
                                 <span class="px-2 py-0.5 bg-slate-100 text-slate-600 font-mono text-xs font-bold rounded border border-slate-200">{{ table.physical_name }}</span>
                                 <span class="text-[10px] text-gray-400">只读</span>
                              </div>
                              <div class="rounded-lg border-2 border-dashed border-amber-300/80 bg-amber-50/20 p-3 space-y-2">
                                 <div class="flex items-center gap-1 text-[10px] font-bold text-amber-700 uppercase tracking-wider">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
                                    可编辑区域
                                 </div>
                                 <div>
                                    <label class="block text-[10px] font-bold text-gray-500 mb-1">业务名称</label>
                                    <input
                                      v-model="table.term"
                                      :class="EDITABLE_INPUT_SM"
                                      placeholder="例如：数据库列级权限控制表"
                                    >
                                 </div>
                                 <div>
                                    <label class="block text-[10px] font-bold text-gray-500 mb-1">表用途描述</label>
                                    <textarea
                                      v-model="table.description"
                                      :rows="getDescriptionRows(table.description)"
                                      :class="EDITABLE_TEXTAREA"
                                      placeholder="请补充或修改表的用途说明..."
                                    />
                                 </div>
                              </div>
                           </div>
                           <div class="flex items-center gap-1 shrink-0">
                              <button
                                 type="button"
                                 class="text-gray-400 hover:text-gray-600 p-1.5 rounded-md hover:bg-gray-50 transition-colors"
                                 :class="expandedPreviewTables[idx] ? 'rotate-90' : ''"
                                 @click="togglePreviewTableExpand(idx)"
                                 title="展开字段画像"
                              >
                                 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"/></svg>
                              </button>
                              <button @click="removeTable(idx)" class="text-gray-300 hover:text-red-500 p-1.5 rounded-md hover:bg-red-50 transition-colors">
                                 <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                              </button>
                           </div>
                        </div>

                        <div v-if="!expandedPreviewTables[idx]" class="flex flex-wrap gap-1.5">
                           <div
                              v-for="(col, cIdx) in table.columns.slice(0, 10)"
                              :key="cIdx"
                              class="flex items-center gap-1 px-2 py-0.5 bg-slate-50 border border-slate-100 rounded-md text-[10px] font-mono"
                           >
                              <span class="text-slate-600 font-bold">{{ col.physical_name }}</span>
                              <span class="text-slate-300">/</span>
                              <span class="text-blue-500">{{ col.term || '?' }}</span>
                           </div>
                           <div
                              v-if="table.columns.length > 10"
                              class="px-2 py-0.5 bg-gray-50 border border-gray-100 rounded-md text-[10px] text-gray-400 font-medium"
                           >
                              +{{ table.columns.length - 10 }} 更多字段
                           </div>
                           <button
                              v-if="table.columns.length > 0"
                              type="button"
                              @click="togglePreviewTableExpand(idx)"
                              class="px-2 py-0.5 text-[10px] text-blue-500 hover:bg-blue-50 rounded-md border border-transparent hover:border-blue-100 transition-colors"
                           >
                              展开字段画像
                           </button>
                        </div>
                     </div>

                     <div v-if="expandedPreviewTables[idx]" class="border-t border-amber-200/60 p-3 bg-amber-50/10 space-y-2">
                        <div class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                           <span>字段画像定义 (Columns Profile)</span>
                           <span class="text-gray-300 font-normal">· {{ table.columns.length }} 个字段</span>
                           <span class="text-[10px] font-semibold text-amber-600 normal-case tracking-normal">虚线框可编辑</span>
                        </div>

                        <div class="border-2 border-dashed border-amber-300/70 rounded-xl overflow-hidden bg-white">
                           <table class="w-full text-left border-collapse text-xs mb-3">
                              <thead>
                                 <tr class="bg-amber-50/80 border-b border-amber-200/60 text-gray-500 font-bold uppercase">
                                    <th class="px-4 py-2 border-r border-amber-100 w-[18%]">物理字段</th>
                                    <th class="px-4 py-2 border-r border-amber-100 w-[14%]">类型</th>
                                    <th class="px-4 py-2 border-r border-amber-100 w-[18%]">业务术语</th>
                                    <th class="px-4 py-2 w-[42%]">业务含义说明</th>
                                    <th class="px-4 py-2 w-[8%]"></th>
                                 </tr>
                              </thead>
                              <tbody class="divide-y divide-amber-100/80 text-gray-700">
                                 <tr v-for="(col, cIdx) in table.columns" :key="cIdx" class="hover:bg-amber-50/30">
                                    <td class="px-3 py-2 border-r border-amber-100">
                                       <input v-model="col.physical_name" :class="[EDITABLE_CELL, 'font-mono font-bold']" placeholder="物理名">
                                    </td>
                                    <td class="px-3 py-2 border-r border-amber-100">
                                       <select v-model="col.type" :class="EDITABLE_SELECT">
                                          <option value="String">String</option>
                                          <option value="Int64">Int64</option>
                                          <option value="Float64">Float64</option>
                                          <option value="DateTime">DateTime</option>
                                          <option value="Boolean">Boolean</option>
                                          <option value="JSON">JSON</option>
                                       </select>
                                    </td>
                                    <td class="px-3 py-2 border-r border-amber-100">
                                       <input v-model="col.term" :class="[EDITABLE_CELL, 'text-primary font-medium']" placeholder="业务名">
                                    </td>
                                    <td class="px-3 py-2">
                                       <input v-model="col.description" :class="EDITABLE_CELL" placeholder="描述...">
                                    </td>
                                    <td class="px-3 py-2 text-right">
                                       <button @click="table.columns.splice(cIdx, 1)" class="text-gray-300 hover:text-red-500 transition-colors">
                                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                                       </button>
                                    </td>
                                 </tr>
                              </tbody>
                           </table>
                        </div>

                        <button
                           @click="table.columns.push({ physical_name: '', term: '', type: 'String', description: '' })"
                           class="w-full py-1.5 border-2 border-dashed border-amber-300 rounded-lg text-[10px] text-amber-600 hover:text-amber-700 hover:border-amber-400 hover:bg-amber-50 transition-all flex items-center justify-center gap-1"
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
               <h3 class="text-base font-bold text-gray-900 mb-2 flex items-center gap-2">
                 <span class="w-1.5 h-5 bg-amber-500 rounded-full"></span>
                 业务指标 (Metrics)
               </h3>
               <div class="grid grid-cols-2 gap-3">
                  <div v-for="(metric, idx) in previewData.metrics" :key="idx" class="bg-white rounded-xl border border-gray-200 p-4 shadow-sm group border-l-4 border-l-amber-400">
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
               <h3 class="text-base font-bold text-gray-900 mb-2 flex items-center gap-2">
                 <span class="w-1.5 h-5 bg-purple-500 rounded-full"></span>
                 实体关系 (Relationships)
               </h3>
               <div class="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
                  <table class="min-w-full text-sm">
                     <thead class="bg-gray-50 text-gray-500 font-medium">
                        <tr>
                           <th class="px-3 py-2 text-left">Source</th>
                           <th class="px-3 py-2 text-center">Type</th>
                           <th class="px-3 py-2 text-left">Target</th>
                           <th class="px-3 py-2 text-left">Condition</th>
                           <th class="px-3 py-2"></th>
                        </tr>
                     </thead>
                     <tbody class="divide-y divide-gray-100">
                        <tr v-for="(rel, idx) in previewData.relationships" :key="idx" class="group hover:bg-gray-50">
                           <td class="px-3 py-2 font-mono text-blue-600 font-bold">{{ rel.source_table }}</td>
                           <td class="px-3 py-2 text-center">
                              <span class="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs border border-purple-100">{{ rel.type }}</span>
                           </td>
                           <td class="px-3 py-2 font-mono text-blue-600 font-bold">{{ rel.target_table }}</td>
                           <td class="px-3 py-2 font-mono text-xs text-gray-500">{{ rel.condition }}</td>
                           <td class="px-3 py-2 text-right">
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
      <div v-if="step === 2" class="px-5 py-2.5 border-t border-gray-100 bg-white flex justify-end gap-2 shrink-0">
         <button @click="handleClose" class="px-4 py-1.5 text-sm border border-gray-300 rounded-lg text-gray-700 font-medium hover:bg-gray-50">取消</button>
         <button 
            @click="handleSave"
            :disabled="saving || previewData.tables.length === 0"
            class="px-5 py-1.5 text-sm bg-green-600 hover:bg-green-700 text-white rounded-lg font-bold shadow-sm transition-all flex items-center gap-1.5 disabled:opacity-50"
         >
            <svg v-if="saving" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
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
    :imported-table-names="props.datasetId ? (props.importedTableNames || []) : []"
    :locked-data-source-name="props.datasetId ? (props.datasetDataSource || '') : ''"
    @close="showDbImportModal = false"
    @confirm="handleDbDdlConfirm"
    @confirm-profile="handleProfileImportConfirm"
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

:deep(input[class*="border-dashed"]::placeholder),
:deep(textarea[class*="border-dashed"]::placeholder) {
  color: #d97706;
  opacity: 0.55;
}
</style>
