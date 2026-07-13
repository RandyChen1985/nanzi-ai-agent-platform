<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import axios from '../utils/axios'
import { useToast } from '../composables/useToast'
import SkillFileTree from '../components/SkillFileTree.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import MarkdownIt from 'markdown-it'

interface Skill {
  id: string
  name: string
  description: string
  path: string
}

interface FileNode {
  name: string
  path: string
  is_dir: boolean
  size?: number
  children?: FileNode[]
}

const { showToast } = useToast()

const confirmState = ref({
  show: false,
  title: '',
  message: '',
  type: 'danger' as 'danger' | 'primary' | 'warning',
  onConfirm: () => {}
})

const skills = ref<Skill[]>([])
const loading = ref(false)
const searchQuery = ref('')
const viewMode = ref<'card' | 'list'>('card')

// 新建技能相关
const showCreateModal = ref(false)
const newSkill = ref({
  id: '',
  name: '',
  description: ''
})
const creating = ref(false)

// 统计相关
import * as echarts from 'echarts'
const showStatsModal = ref(false)
const activeTab = ref('distribution')
const loadingStats = ref(false)
const statsData = ref<any>(null)
const distributionChartRef = ref<HTMLElement | null>(null)
const trendChartRef = ref<HTMLElement | null>(null)
let distChartInstance: echarts.ECharts | null = null
let trendChartInstance: echarts.ECharts | null = null

const hasStatsData = computed(() => {
  if (!statsData.value) return false
  return Object.keys(statsData.value.total || {}).length > 0
})

// 帮助弹窗相关
const showHelpModal = ref(false)
const commandCopied = ref(false)

// 详情抽屉 (Drawer) 相关
const showDrawer = ref(false)
const activeSkillId = ref('')
const activeSkillName = ref('')
const activeSkillDesc = ref('')
const fileTree = ref<FileNode[]>([])
const selectedFilePath = ref('SKILL.md') // 当前选中的文件相对路径，默认 SKILL.md
const editingContent = ref('')
const originalContent = ref('')
const saving = ref(false)
const fetchingDetail = ref(false)

// 新建技能资产相关
const selectedDirectoryPath = ref('')
const showCreateAssetModal = ref(false)
const createAssetType = ref<'file' | 'folder'>('file')
const createAssetName = ref('')
const creatingAsset = ref(false)

const createAssetTargetLabel = computed(() => {
  return selectedDirectoryPath.value || '技能根目录'
})

// 拖拽上传相关
const dragActive = ref(false)
const uploadFolder = ref('') // 上传到技能目录下的子文件夹路径 (可选)
const uploading = ref(false)
const uploadType = ref<'normal' | 'archive'>('normal')

// 右键菜单相关
const contextMenu = ref({
  show: false,
  x: 0,
  y: 0,
  node: null as any
})

// 导入技能相关
const showImportModal = ref(false)
const importOverwrite = ref(false)
const importingSkill = ref(false)
const importFile = ref<File | null>(null)
const importDragActive = ref(false)

// 获取技能列表
const fetchSkills = async () => {
  loading.value = true
  try {
    const response = await axios.get('/api/portal/skills')
    if (response.data && response.data.status === 'success') {
      skills.value = response.data.data || []
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取技能列表失败', 'error')
  } finally {
    loading.value = false
  }
}

// 搜索过滤
const filteredSkills = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return skills.value
  return skills.value.filter(s => 
    s.id.toLowerCase().includes(query) ||
    s.name.toLowerCase().includes(query) ||
    s.description.toLowerCase().includes(query)
  )
})

// 复制 CLI 命令
const copyCommand = async () => {
  const cmd = 'npx skills add https://github.com/vercel-labs/skills --skill find-skills'
  try {
    await navigator.clipboard.writeText(cmd)
    commandCopied.value = true
    showToast('命令已成功复制到剪贴板！', 'success')
    setTimeout(() => {
      commandCopied.value = false
    }, 2000)
  } catch (err) {
    showToast('复制失败，请手动选择复制', 'error')
  }
}

// 打开创建弹窗
const openCreateModal = () => {
  newSkill.value = { id: '', name: '', description: '' }
  showCreateModal.value = true
}

// 提交创建
const createSkill = async () => {
  if (!newSkill.value.id.trim()) {
    showToast('技能唯一识别ID不能为空', 'warning')
    return
  }
  if (!/^[a-zA-Z0-9_-]+$/.test(newSkill.value.id)) {
    showToast('技能ID仅支持英文、数字、中划线和下划线', 'warning')
    return
  }
  if (!newSkill.value.name.trim()) {
    showToast('技能名称不能为空', 'warning')
    return
  }

  creating.value = true
  try {
    const response = await axios.post('/api/portal/skills', {
      id: newSkill.value.id.trim(),
      name: newSkill.value.name.trim(),
      description: newSkill.value.description.trim()
    })
    if (response.data && response.data.status === 'success') {
      showToast('技能创建成功！', 'success')
      showCreateModal.value = false
      fetchSkills()
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '新建技能失败', 'error')
  } finally {
    creating.value = false
  }
}

// 物理删除整个技能（注销）
const deleteSkill = (skillId: string) => {
  confirmState.value = {
    show: true,
    title: '彻底删除技能',
    message: `确定要彻底删除技能 [${skillId}] 吗？\n该操作会物理删除对应的技能目录及其所有子资产脚本，且不可恢复！`,
    type: 'danger',
    onConfirm: async () => {
      try {
        const response = await axios.delete(`/api/portal/skills/${skillId}`)
        if (response.data && response.data.status === 'success') {
          showToast(`技能 ${skillId} 已成功物理删除`, 'success')
          fetchSkills()
          if (activeSkillId.value === skillId) {
            showDrawer.value = false
          }
        }
      } catch (e: any) {
        showToast(e.response?.data?.detail || '注销技能失败', 'error')
      } finally {
        confirmState.value.show = false
      }
    }
  }
}

// 查看技能详情与抽屉挂载
const openSkillDetail = async (skillId: string) => {
  activeSkillId.value = skillId
  selectedFilePath.value = 'SKILL.md'
  selectedDirectoryPath.value = ''
  showDrawer.value = true
  await fetchSkillDetail(skillId)
}

// 获取详情 (包含加载选中的文件内容)
const fetchSkillDetail = async (skillId: string) => {
  fetchingDetail.value = true
  try {
    const response = await axios.get(`/api/portal/skills/${skillId}`)
    if (response.data && response.data.status === 'success') {
      const data = response.data.data
      activeSkillName.value = data.name
      activeSkillDesc.value = data.description
      fileTree.value = data.file_tree || []
      
      // 默认加载 SKILL.md
      if (selectedFilePath.value === 'SKILL.md') {
        editingContent.value = data.skill_md_content || ''
        originalContent.value = data.skill_md_content || ''
      } else {
        // 如果选中了其他文件，递归查找读取它
        await loadFileContent(skillId, selectedFilePath.value)
      }
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取技能详情失败', 'error')
    showDrawer.value = false
  } finally {
    fetchingDetail.value = false
  }
}

// 在线读取特定资产文件内容
const loadFileContent = async (skillId: string, filePath: string) => {
  fetchingDetail.value = true
  try {
    const response = await axios.get(`/api/portal/skills/${skillId}/files`, {
      params: { path: filePath }
    })
    if (response.data && response.data.status === 'success') {
      editingContent.value = response.data.content || ''
      originalContent.value = response.data.content || ''
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || `读取文件 ${filePath} 失败`, 'error')
    // 降级回 SKILL.md
    selectedFilePath.value = 'SKILL.md'
    await fetchSkillDetail(skillId)
  } finally {
    fetchingDetail.value = false
  }
}

// 选中某个文件进行编辑
const selectFileForEdit = async (path: string) => {
  const doSelect = async () => {
    selectedFilePath.value = path
    editorMode.value = 'edit'
    if (path === 'SKILL.md') {
      await fetchSkillDetail(activeSkillId.value)
    } else {
      await loadFileContent(activeSkillId.value, path)
    }
  }

  if (hasUnsavedChanges.value) {
    confirmState.value = {
      show: true,
      title: '未保存修改确认',
      message: '当前文件有未保存的修改，切换文件将丢失这些修改。确定要切换吗？',
      type: 'warning',
      onConfirm: async () => {
        confirmState.value.show = false
        await doSelect()
      }
    }
    return
  }
  await doSelect()
}

const selectDirectory = (path: string) => {
  selectedDirectoryPath.value = path
}

const openCreateAssetModal = (type: 'file' | 'folder') => {
  if (type === 'file' && hasUnsavedChanges.value) {
    showToast('请先保存当前文件的修改，再新建文件', 'warning')
    return
  }
  createAssetType.value = type
  createAssetName.value = ''
  showCreateAssetModal.value = true
}

const createSkillAsset = async () => {
  const name = createAssetName.value.trim()
  if (!name) {
    showToast('资产名称不能为空', 'warning')
    return
  }
  if (name.startsWith('.') || name.includes('/') || name.includes('\\')) {
    showToast('请输入非隐藏的单级文件或文件夹名称', 'warning')
    return
  }

  const path = selectedDirectoryPath.value
    ? `${selectedDirectoryPath.value}/${name}`
    : name

  creatingAsset.value = true
  try {
    const response = await axios.post(`/api/portal/skills/${activeSkillId.value}/files`, {
      path,
      type: createAssetType.value
    })
    if (response.data?.status === 'success') {
      showToast(createAssetType.value === 'file' ? '文件创建成功' : '文件夹创建成功', 'success')
      showCreateAssetModal.value = false
      if (createAssetType.value === 'file') {
        selectedFilePath.value = path
      } else {
        selectedDirectoryPath.value = path
      }
      await fetchSkillDetail(activeSkillId.value)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '创建资产失败', 'error')
  } finally {
    creatingAsset.value = false
  }
}

// 是否有未保存的修改
const hasUnsavedChanges = computed(() => {
  return editingContent.value !== originalContent.value
})

// 计算行数以显示在编辑器状态栏
const lineCount = computed(() => {
  return editingContent.value.split('\n').length || 1
})

const charCount = computed(() => editingContent.value.length)

const selectedFileExtension = computed(() => {
  const parts = selectedFilePath.value.split('.')
  return parts.length > 1 ? parts.pop()!.toLowerCase() : 'txt'
})

const editorTextareaRef = ref<HTMLTextAreaElement | null>(null)
const lineNumbersRef = ref<HTMLDivElement | null>(null)

const syncLineNumberScroll = () => {
  if (lineNumbersRef.value && editorTextareaRef.value) {
    lineNumbersRef.value.scrollTop = editorTextareaRef.value.scrollTop
  }
}

const handleEditorKeydown = (e: KeyboardEvent) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault()
    if (hasUnsavedChanges.value && !saving.value) {
      saveFileContent()
    }
  }
}

const mdParser = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true
})

const editorMode = ref<'edit' | 'preview'>('edit')

const isMarkdownFile = computed(() => {
  return selectedFilePath.value.toLowerCase().endsWith('.md')
})

const renderedMarkdown = computed(() => {
  if (!editingContent.value) return '<p class="preview-empty">暂无内容，请先在编辑视图中编写</p>'
  try {
    return mdParser.render(editingContent.value)
  } catch (e) {
    return `<p class="text-red-400 p-4">渲染解析 Markdown 时遇到错误: ${e}</p>`
  }
})

// 保存编辑的文件内容
const saveFileContent = async () => {
  saving.value = true
  try {
    const response = await axios.put(`/api/portal/skills/${activeSkillId.value}/files`, {
      path: selectedFilePath.value,
      content: editingContent.value
    })
    if (response.data && response.data.status === 'success') {
      showToast('文件保存成功', 'success')
      originalContent.value = editingContent.value
      // 重新获取详情以更新文件树大小
      await fetchSkillDetail(activeSkillId.value)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

// 删除技能目录下的某个子文件或子目录
const deleteSkillFile = (filePath: string) => {
  confirmState.value = {
    show: true,
    title: '删除资产文件',
    message: `确定要删除物理资产 [${filePath}] 吗？\n该删除操作无法撤销！`,
    type: 'danger',
    onConfirm: async () => {
      try {
        const response = await axios.delete(`/api/portal/skills/${activeSkillId.value}/files`, {
          params: { path: filePath }
        })
        if (response.data && response.data.status === 'success') {
          showToast('资产已成功物理删除', 'success')
          if (
            selectedFilePath.value === filePath ||
            selectedFilePath.value.startsWith(`${filePath}/`)
          ) {
            selectedFilePath.value = 'SKILL.md'
          }
          if (
            selectedDirectoryPath.value === filePath ||
            selectedDirectoryPath.value.startsWith(`${filePath}/`)
          ) {
            selectedDirectoryPath.value = ''
          }
          await fetchSkillDetail(activeSkillId.value)
        }
      } catch (e: any) {
        showToast(e.response?.data?.detail || '删除资产失败', 'error')
      } finally {
        confirmState.value.show = false
      }
    }
  }
}

// 上传资产文件
const handleFileUpload = async (event: Event) => {
  const target = event.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return
  await uploadFiles(files)
}

// 处理拖拽
const handleDragOver = (e: DragEvent) => {
  e.preventDefault()
  dragActive.value = true
}

const handleDragLeave = (e: DragEvent) => {
  e.preventDefault()
  dragActive.value = false
}

const handleDrop = async (e: DragEvent) => {
  e.preventDefault()
  dragActive.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    await uploadFiles(files)
  }
}

// 物理上传执行 (单文件限 10MB / 压缩包限 20MB)
const uploadFiles = async (files: FileList) => {
  uploading.value = true
  try {
    for (const file of Array.from(files)) {
      const isArchive = uploadType.value === 'archive'
      const limit = isArchive ? 20 * 1024 * 1024 : 10 * 1024 * 1024
      if (file.size > limit) {
        showToast(`文件 ${file.name} 超过限制大小 (${isArchive ? '20MB' : '10MB'})，已拦截上传`, 'warning')
        continue
      }

      const formData = new FormData()
      formData.append('file', file)
      if (uploadFolder.value.trim()) {
        formData.append('folder', uploadFolder.value.trim())
      }

      if (isArchive) {
        await axios.post(`/api/portal/skills/${activeSkillId.value}/upload-archive`, formData)
        showToast(`压缩包 ${file.name} 上传解压成功！`, 'success')
      } else {
        await axios.post(`/api/portal/skills/${activeSkillId.value}/upload`, formData)
        showToast(`文件 ${file.name} 上传成功！`, 'success')
      }
    }
    // 重置上传目录与状态并重新获取详情更新文件树
    uploadFolder.value = ''
    uploadType.value = 'normal'
    await fetchSkillDetail(activeSkillId.value)
  } catch (e: any) {
    showToast(e.response?.data?.detail || '文件上传遇到错误', 'error')
  } finally {
    uploading.value = false
  }
}

const openStatsModal = async () => {
  showStatsModal.value = true
  loadingStats.value = true
  try {
    const res = await axios.get('/api/portal/skills/stats')
    statsData.value = res.data
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取统计数据失败', 'error')
  } finally {
    loadingStats.value = false
  }
  
  nextTick(() => {
    initCharts()
  })
}

const closeStatsModal = () => {
  showStatsModal.value = false
  activeTab.value = 'distribution'
  if (distChartInstance) {
    distChartInstance.dispose()
    distChartInstance = null
  }
  if (trendChartInstance) {
    trendChartInstance.dispose()
    trendChartInstance = null
  }
}

const switchTab = (tab: string) => {
  activeTab.value = tab
  nextTick(() => {
    initCharts()
  })
}

const initCharts = () => {
  if (!hasStatsData.value) return
  
  if (activeTab.value === 'distribution') {
    // 1. 分布饼图
    if (distributionChartRef.value) {
      if (!distChartInstance) {
        distChartInstance = echarts.init(distributionChartRef.value)
        const chartData = Object.entries(statsData.value.total).map(([name, value]) => ({
          name,
          value
        }))
        
        distChartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: '{b}: {c} 次 ({d}%)'
          },
          legend: {
            orient: 'horizontal',
            bottom: 0,
            type: 'scroll'
          },
          series: [
            {
              name: '激活次数',
              type: 'pie',
              radius: ['45%', '70%'],
              avoidLabelOverlap: true,
              itemStyle: {
                borderRadius: 8,
                borderColor: '#fff',
                borderWidth: 2
              },
              label: {
                show: false,
                position: 'center'
              },
              emphasis: {
                label: {
                  show: true,
                  fontSize: 14,
                  fontWeight: 'bold'
                }
              },
              data: chartData
            }
          ]
        })
      } else {
        distChartInstance.resize()
      }
    }
  } else if (activeTab.value === 'trend') {
    // 2. 趋势折线图
    if (trendChartRef.value) {
      if (!trendChartInstance) {
        trendChartInstance = echarts.init(trendChartRef.value)
        const dates = Object.keys(statsData.value.trend).sort()
        const skillIds = Object.keys(statsData.value.total)
        
        const series = skillIds.map(skillId => {
          const data = dates.map(date => statsData.value.trend[date][skillId] || 0)
          return {
            name: skillId,
            type: 'line',
            smooth: true,
            data
          }
        })
        
        trendChartInstance.setOption({
          tooltip: {
            trigger: 'axis'
          },
          legend: {
            data: skillIds,
            bottom: 0,
            type: 'scroll'
          },
          grid: {
            left: '3%',
            right: '4%',
            bottom: '12%',
            containLabel: true
          },
          xAxis: {
            type: 'category',
            boundaryGap: false,
            data: dates.map(d => d.substring(5))
          },
          yAxis: {
            type: 'value',
            minInterval: 1
          },
          series
        })
      } else {
        trendChartInstance.resize()
      }
    }
  }
}

const closeContextMenu = () => {
  contextMenu.value.show = false
}

const handleContextMenu = (data: { event: MouseEvent, node: any }) => {
  contextMenu.value.show = true
  contextMenu.value.x = data.event.clientX
  contextMenu.value.y = data.event.clientY
  contextMenu.value.node = data.node
}

const handleEmptyAreaContextMenu = (e: MouseEvent) => {
  contextMenu.value.show = true
  contextMenu.value.x = e.clientX
  contextMenu.value.y = e.clientY
  contextMenu.value.node = {
    name: '技能根目录',
    path: '',
    is_dir: true
  }
}

const getParentDirectory = (path: string) => {
  if (!path || !path.includes('/')) return ''
  return path.substring(0, path.lastIndexOf('/'))
}

const handleMenuAction = async (action: 'new-file' | 'new-folder' | 'upload-normal' | 'upload-archive' | 'delete' | 'edit') => {
  const node = contextMenu.value.node
  if (!node) return
  
  closeContextMenu()

  if (action === 'new-file') {
    selectedDirectoryPath.value = node.is_dir ? node.path : getParentDirectory(node.path)
    openCreateAssetModal('file')
  } else if (action === 'new-folder') {
    selectedDirectoryPath.value = node.is_dir ? node.path : getParentDirectory(node.path)
    openCreateAssetModal('folder')
  } else if (action === 'upload-normal') {
    uploadFolder.value = node.is_dir ? node.path : getParentDirectory(node.path)
    uploadType.value = 'normal'
    triggerFileInput()
  } else if (action === 'upload-archive') {
    uploadFolder.value = node.is_dir ? node.path : getParentDirectory(node.path)
    uploadType.value = 'archive'
    triggerFileInput()
  } else if (action === 'delete') {
    await deleteSkillFile(node.path)
  } else if (action === 'edit') {
    await selectFileForEdit(node.path)
  }
}

const triggerFileInput = () => {
  nextTick(() => {
    const input = document.getElementById('file-upload-input') as HTMLInputElement
    if (input) {
      input.click()
    }
  })
}

const openImportModal = () => {
  importFile.value = null
  importOverwrite.value = false
  showImportModal.value = true
}

const handleImportFileChange = (e: Event) => {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (files && files.length > 0) {
    const file = files[0]
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
    if (!['.zip', '.tar', '.gz', '.tgz', '.bz2'].includes(ext)) {
      showToast('仅支持 .zip, .tar, .tar.gz, .tgz 等压缩包格式', 'warning')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      showToast('导入技能包不能超过 20MB', 'warning')
      return
    }
    importFile.value = file
  }
}

const handleImportDragOver = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = true
}

const handleImportDragLeave = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = false
}

const handleImportDrop = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    const file = files[0]
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
    if (!['.zip', '.tar', '.gz', '.tgz', '.bz2'].includes(ext)) {
      showToast('仅支持 .zip, .tar, .tar.gz, .tgz 等压缩包格式', 'warning')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      showToast('导入技能包不能超过 20MB', 'warning')
      return
    }
    importFile.value = file
  }
}

const submitImportSkill = async () => {
  if (!importFile.value) {
    showToast('请选择或拖入要导入的压缩技能包', 'warning')
    return
  }

  importingSkill.value = true
  try {
    const formData = new FormData()
    formData.append('file', importFile.value)
    formData.append('overwrite', importOverwrite.value ? 'true' : 'false')

    const response = await axios.post('/api/portal/skills/import', formData)
    if (response.data?.status === 'success') {
      showToast(response.data?.message || '技能导入成功！', 'success')
      showImportModal.value = false
      importFile.value = null
      importOverwrite.value = false
      await fetchSkills()
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '技能导入失败', 'error')
  } finally {
    importingSkill.value = false
  }
}

onMounted(() => {
  fetchSkills()
  document.addEventListener('click', closeContextMenu)
})

onUnmounted(() => {
  document.removeEventListener('click', closeContextMenu)
})
</script>

<template>
  <div class="space-y-5">
    <!-- Header 头部栏 -->
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
      <div class="flex items-center space-x-3">
        <h1 class="text-2xl font-bold tracking-normal text-gray-900">技能工作台</h1>

        <!-- 「？」呼吸帮助按钮 -->
        <button 
          @click="showHelpModal = true"
          class="flex items-center justify-center w-7 h-7 rounded-full bg-white text-blue-600 border border-gray-200 hover:border-blue-300 hover:bg-blue-50 transition-colors shadow-sm"
          title="技能开发使用指南"
        >
          <span class="font-bold text-sm">?</span>
        </button>
      </div>

      <!-- 右侧控制区 -->
      <div class="flex flex-col sm:flex-row sm:items-center gap-3">
        <div class="relative w-full sm:w-72">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input 
            v-model="searchQuery"
            type="text" 
            placeholder="搜索技能名称或描述..." 
            class="w-full pl-9 pr-4 py-2 bg-white border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm transition-all shadow-sm"
          />
        </div>
        <!-- 视图切换按钮 -->
        <div class="flex items-center bg-gray-200/60 p-0.5 rounded-lg border border-gray-300 gap-0.5 select-none shrink-0">
          <button 
            @click="viewMode = 'card'"
            class="p-1.5 rounded-md transition-all duration-200 flex items-center justify-center"
            :class="viewMode === 'card' ? 'bg-white text-blue-600 shadow-sm border border-gray-200' : 'text-gray-500 hover:text-gray-800 hover:bg-gray-150'"
            title="卡片视图"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
            </svg>
          </button>
          <button 
            @click="viewMode = 'list'"
            class="p-1.5 rounded-md transition-all duration-200 flex items-center justify-center"
            :class="viewMode === 'list' ? 'bg-white text-blue-600 shadow-sm border border-gray-200' : 'text-gray-500 hover:text-gray-800 hover:bg-gray-150'"
            title="列表视图"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>
        <button 
          @click="openStatsModal"
          class="flex items-center justify-center gap-2 px-4 py-2 bg-white text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 active:scale-95 transition-all shadow-sm font-medium text-sm"
        >
          <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          统计查看
        </button>
        <button 
          @click="openImportModal"
          class="flex items-center justify-center gap-2 px-4 py-2 bg-white text-blue-600 border border-blue-200 hover:bg-blue-50 active:scale-95 transition-all shadow-sm font-semibold text-sm rounded-lg"
        >
          <svg class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          导入技能 (Zip/Tar)
        </button>
        <button 
          @click="openCreateModal"
          class="flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 active:scale-95 transition-all shadow-sm font-medium text-sm"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          新建技能
        </button>
      </div>
    </div>

    <!-- 技能列表网格 -->
    <div v-if="loading" class="flex flex-col items-center justify-center py-16">
      <div class="w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
      <p class="text-sm text-gray-500 mt-4 font-medium">智能检索系统文件夹中...</p>
    </div>

    <div v-else-if="filteredSkills.length === 0" class="flex flex-col items-center justify-center min-h-[360px] bg-white border border-gray-200 rounded-lg shadow-sm">
      <svg class="w-14 h-14 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
      <p class="text-sm text-gray-500 mt-4 font-semibold">未发现匹配的智能体技能目录</p>
      <p class="text-xs text-gray-400 mt-1">您可以新建一个技能，或参考帮助文档安装生态技能</p>
    </div>

    <!-- 卡片视图 -->
    <div v-else-if="viewMode === 'card'" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
      <!-- 磨砂玻璃质感卡片 -->
      <div 
        v-for="skill in filteredSkills" 
        :key="skill.id"
        @click="openSkillDetail(skill.id)"
        class="group relative flex flex-col bg-white border border-gray-200 hover:border-blue-400 hover:shadow-lg hover:-translate-y-0.5 rounded-lg p-5 cursor-pointer transition-all duration-200"
      >
        <!-- 卡片顶部 -->
        <div class="flex items-start justify-between">
          <div class="flex items-center space-x-3 min-w-0">
            <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500/10 to-indigo-500/10 text-blue-600 group-hover:scale-110 transition-transform">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <div class="min-w-0">
              <h3 class="font-bold text-gray-800 truncate group-hover:text-blue-600 transition-colors">
                {{ skill.name }}
              </h3>
              <p class="text-[10px] text-gray-400 font-mono tracking-wider uppercase mt-0.5">
                ID: {{ skill.id }}
              </p>
            </div>
          </div>
          
          <!-- 删除物理技能按钮 -->
          <button 
            @click.stop="deleteSkill(skill.id)"
            class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            title="注销并彻底删除技能目录"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>

        <!-- 技能描述 -->
        <p class="text-sm text-gray-500 mt-4 flex-1 line-clamp-3">
          {{ skill.description || '暂无描述' }}
        </p>

        <!-- 卡片底部路径信息 -->
        <div class="flex items-center gap-1.5 text-[11px] text-gray-400 font-mono mt-5 border-t border-gray-100 pt-3">
          <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          <span class="truncate" :title="skill.path">{{ skill.path }}</span>
        </div>
      </div>
    </div>

    <!-- 列表视图 -->
    <div v-else class="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200 text-left">
          <thead class="bg-gray-50/70 text-xs font-bold text-gray-500 uppercase tracking-wider select-none">
            <tr>
              <th class="px-6 py-4">技能名称与描述</th>
              <th class="px-6 py-4 text-center">物理映射路径</th>
              <th class="px-6 py-4 text-center">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 text-sm">
            <tr 
              v-for="skill in filteredSkills" 
              :key="skill.id"
              @click="openSkillDetail(skill.id)"
              class="hover:bg-blue-50/20 cursor-pointer transition-colors group"
            >
              <td class="px-6 py-4">
                <div class="flex items-start space-x-3">
                  <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-50 text-blue-600 group-hover:scale-105 transition-transform flex-shrink-0 mt-0.5">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                    </svg>
                  </div>
                  <div class="space-y-0.5">
                    <div class="font-bold text-gray-900 group-hover:text-blue-600 transition-colors flex items-center gap-2 flex-wrap">
                      <span>{{ skill.name }}</span>
                      <span class="text-[10px] text-gray-400 font-mono bg-gray-100 px-1.5 py-0.5 rounded border border-gray-200 font-normal">ID: {{ skill.id }}</span>
                    </div>
                    <div class="text-xs text-gray-500 max-w-2xl line-clamp-2" :title="skill.description">
                      {{ skill.description || '暂无描述' }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="px-6 py-4 text-center font-mono text-xs text-gray-500 max-w-xs truncate" :title="skill.path">
                {{ skill.path }}
              </td>
              <td class="px-6 py-4 text-center whitespace-nowrap" @click.stop>
                <div class="flex items-center justify-center space-x-2 whitespace-nowrap">
                  <button 
                    @click="openSkillDetail(skill.id)"
                    class="px-3 py-1.5 text-xs font-semibold border border-blue-200 text-blue-600 hover:bg-blue-50 hover:border-blue-300 rounded-lg transition-colors whitespace-nowrap shrink-0"
                  >
                    配置
                  </button>
                  <button 
                    @click="deleteSkill(skill.id)"
                    class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors whitespace-nowrap shrink-0"
                    title="注销并彻底删除技能目录"
                  >
                    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 「？」帮助介绍弹窗 Modal -->
    <div 
      v-if="showHelpModal" 
      class="fixed inset-0 bg-black/50 backdrop-blur-sm z-[9990] flex items-center justify-center p-4 animate-fade-in"
      @click.self="showHelpModal = false"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-2xl w-full p-6 sm:p-8 flex flex-col max-h-[85vh] overflow-y-auto border border-gray-150 relative">
        <!-- 关闭按钮 -->
        <button 
          @click="showHelpModal = false"
          class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div class="space-y-6">
          <div class="flex items-center space-x-3 border-b border-gray-100 pb-4">
            <div class="w-8 h-8 rounded-lg bg-blue-500 text-white flex items-center justify-center font-black">?</div>
            <h2 class="text-xl font-bold text-gray-800">使用生态技能扩展智能体能力</h2>
          </div>

          <!-- Section 1 -->
          <div>
            <h3 class="text-sm font-bold text-gray-900 mb-1.5 flex items-center gap-1.5">
              <span class="w-1.5 h-3 bg-blue-500 rounded-full"></span> 什么是 Skills 技能
            </h3>
            <p class="text-xs text-gray-600 leading-relaxed pl-3">
              Skills 是面向 AI 智能体的一套高阶执行规范与指令约束。云枢智能体平台支持在宿主机物理装载技能。通过物理装载与感知，大模型能够感知技能内部的脚本和守则，完成如网页渲染快照、子进程管理、沙箱 SQL 分析等底层任务。
            </p>
          </div>

          <!-- Section 2 -->
          <div>
            <h3 class="text-sm font-bold text-gray-900 mb-1.5 flex items-center gap-1.5">
              <span class="w-1.5 h-3 bg-blue-500 rounded-full"></span> 命令行快捷下载安装 (宿主机)
            </h3>
            <p class="text-xs text-gray-600 leading-relaxed pl-3 mb-2.5">
              本平台的技能目录已映射到宿主机的 <code class="bg-gray-100 px-1 py-0.5 rounded font-mono text-blue-600">~/.agents/skills/</code> 路径。您可以使用 <code class="bg-gray-100 px-1 py-0.5 rounded font-mono text-indigo-600">npx skills</code> 命令在此目录下下载生态内的高质量共享技能：
            </p>
            
            <!-- 演示指令框 -->
            <div class="bg-slate-950 text-slate-200 rounded-xl p-4 font-mono text-[11px] sm:text-xs flex items-center justify-between border border-slate-800 relative group">
              <div class="overflow-x-auto pr-10 whitespace-nowrap select-all leading-relaxed">
                <span class="text-slate-500">$</span> npx skills add https://github.com/vercel-labs/skills --skill find-skills
              </div>
              <button 
                @click="copyCommand"
                class="absolute right-3 top-3 p-1.5 bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded border border-slate-800 transition-colors flex items-center justify-center"
                title="复制命令"
              >
                <svg v-if="!commandCopied" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                </svg>
                <svg v-else class="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                </svg>
              </button>
            </div>
          </div>

          <!-- Section 3 生态外链按钮 -->
          <div class="border-t border-gray-100 pt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <span class="text-[11px] text-gray-400">想寻找更多好玩的 AI 技能？前往官方市场发现并下载</span>
            <a 
              href="https://www.skills.sh/" 
              target="_blank" 
              class="market-link inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl font-bold text-xs shadow-md shadow-blue-500/20 active:scale-95 transition-all text-center"
            >
              <span>💡 前往官方 Skills 开放市场</span>
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建技能 Modal -->
    <div 
      v-if="showCreateModal" 
      class="fixed inset-0 bg-black/50 backdrop-blur-sm z-[9990] flex items-center justify-center p-4 animate-fade-in"
      @click.self="showCreateModal = false"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-150">
        <h2 class="text-xl font-bold text-gray-800 mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
          初始化智能体技能
        </h2>
        <div class="space-y-4">
          <div>
            <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
              唯一识别 ID (英文标识) <span class="text-red-500">*</span>
            </label>
            <input 
              v-model="newSkill.id"
              type="text" 
              placeholder="例如: search-helper" 
              class="w-full px-3 py-2 border border-gray-350 rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm transition-all"
            />
            <span class="text-[10px] text-gray-400 mt-1 block">物理目录名，创建后不可修改</span>
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
              技能名称 <span class="text-red-500">*</span>
            </label>
            <input 
              v-model="newSkill.name"
              type="text" 
              placeholder="例如: 全局网页搜索辅助" 
              class="w-full px-3 py-2 border border-gray-350 rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm transition-all"
            />
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
              描述说明
            </label>
            <textarea 
              v-model="newSkill.description"
              rows="3"
              placeholder="描述此技能的核心能力或针对的典型任务约束..." 
              class="w-full px-3 py-2 border border-gray-350 rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm transition-all"
            ></textarea>
          </div>
        </div>

        <div class="flex justify-end gap-3 mt-6 border-t border-gray-100 pt-4">
          <button 
            @click="showCreateModal = false"
            class="px-4 py-2 border border-gray-300 text-gray-600 rounded-xl hover:bg-gray-50 transition-colors text-sm font-medium"
          >
            取消
          </button>
          <button 
            @click="createSkill"
            :disabled="creating"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-xl transition-colors text-sm font-medium"
          >
            {{ creating ? '创建中...' : '提交初始化' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情大抽屉 Drawer (从右侧滑出) -->
    <div 
      v-if="showDrawer"
      class="fixed inset-0 z-[9950] overflow-hidden"
    >
      <!-- 蒙层 -->
      <div 
        class="absolute inset-0 bg-black/30 backdrop-blur-[2px] transition-opacity"
        @click="showDrawer = false"
      ></div>

      <!-- 抽屉主体 -->
      <div class="absolute inset-y-0 right-0 pl-10 max-w-full flex">
        <div class="w-screen max-w-6xl bg-white shadow-2xl flex flex-col h-full border-l border-gray-150 animate-slide-in">
          <!-- 抽屉头部 -->
          <div class="px-6 py-4 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
            <div class="flex items-center space-x-3 min-w-0">
              <div class="w-8 h-8 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center flex-shrink-0">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div class="min-w-0">
                <h2 class="text-lg font-bold text-gray-800 truncate">{{ activeSkillName }}</h2>
                <p class="text-xs text-gray-500 font-mono truncate">ID: {{ activeSkillId }}</p>
              </div>
            </div>
            
            <button 
              @click="showDrawer = false"
              class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- 抽屉主要内容，左右分栏 -->
          <div class="flex-1 flex overflow-hidden min-h-0">
            <!-- 左侧：在线编辑器 -->
            <div class="flex-[1.8] flex flex-col border-r border-gray-150 p-5 bg-white overflow-hidden min-h-0">
              <div v-if="fetchingDetail" class="flex-1 flex flex-col items-center justify-center py-20">
                <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                <p class="text-xs text-gray-400 mt-3 font-medium">载入文件内容...</p>
              </div>

              <div v-else class="skill-editor flex-1 flex flex-col min-h-0">
                <!-- 工具栏 -->
                <div class="skill-editor-toolbar">
                  <div class="skill-editor-file-info">
                    <span class="skill-editor-file-icon" :class="`ext-${selectedFileExtension}`">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </span>
                    <span class="skill-editor-filename" :title="selectedFilePath">{{ selectedFilePath }}</span>
                    <span v-if="hasUnsavedChanges" class="skill-editor-unsaved" title="有未保存的修改">未保存</span>
                  </div>

                  <div class="skill-editor-actions">
                    <div v-if="isMarkdownFile" class="skill-editor-mode-toggle">
                      <button
                        type="button"
                        @click="editorMode = 'edit'"
                        :class="{ active: editorMode === 'edit' }"
                      >
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                        编辑
                      </button>
                      <button
                        type="button"
                        @click="editorMode = 'preview'"
                        :class="{ active: editorMode === 'preview' }"
                      >
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                        预览
                      </button>
                    </div>

                    <button
                      type="button"
                      @click="saveFileContent"
                      :disabled="saving || !hasUnsavedChanges"
                      class="skill-editor-save-btn"
                      :class="{ 'is-dirty': hasUnsavedChanges, 'is-saving': saving }"
                      title="保存 (⌘S / Ctrl+S)"
                    >
                      <span v-if="saving" class="skill-editor-save-spinner"></span>
                      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
                      </svg>
                      {{ saving ? '保存中' : '保存' }}
                    </button>
                  </div>
                </div>

                <!-- 编辑区 -->
                <div class="skill-editor-body">
                  <div v-if="editorMode === 'edit'" class="skill-editor-code-pane">
                    <div ref="lineNumbersRef" class="skill-editor-gutter">
                      <div v-for="n in lineCount" :key="n" class="skill-editor-line-num">{{ n }}</div>
                    </div>
                    <textarea
                      ref="editorTextareaRef"
                      v-model="editingContent"
                      @scroll="syncLineNumberScroll"
                      @keydown="handleEditorKeydown"
                      class="skill-editor-textarea"
                      spellcheck="false"
                      placeholder="在此输入文件内容..."
                    ></textarea>
                  </div>

                  <div
                    v-else
                    class="skill-editor-preview markdown-preview"
                    v-html="renderedMarkdown"
                  ></div>
                </div>

                <!-- 状态栏 -->
                <div class="skill-editor-statusbar">
                  <div class="skill-editor-status-left">
                    <span>{{ isMarkdownFile ? 'Markdown' : selectedFileExtension.toUpperCase() }}</span>
                    <span class="skill-editor-status-sep">·</span>
                    <span>UTF-8</span>
                    <span v-if="hasUnsavedChanges" class="skill-editor-status-dirty">· 已修改</span>
                  </div>
                  <div class="skill-editor-status-right">
                    <span>{{ lineCount }} 行</span>
                    <span class="skill-editor-status-sep">·</span>
                    <span>{{ charCount }} 字符</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 右侧：文件资产与脚本上传 (占据 35%) -->
            <div class="flex-1 flex flex-col p-6 overflow-y-auto">
              <!-- 隐藏的物理资产上传接收器 -->
              <input 
                type="file" 
                id="file-upload-input" 
                multiple 
                @change="handleFileUpload" 
                :accept="uploadType === 'archive' ? '.zip,.tar,.gz,.tgz,.bz2' : '*'"
                class="hidden"
              />

              <div class="flex items-center justify-between gap-3 mb-3 select-none shrink-0">
                <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wider">技能资产物理树</h3>
                <span class="flex items-center gap-1.5 text-[10px] text-slate-500 bg-white border border-slate-200 px-2 py-0.5 rounded-full font-medium shadow-sm transition-all hover:border-slate-350" title="鼠标右键点击文件或空白区域可进行新建或上传">
                  <span class="relative flex h-1.5 w-1.5">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                  </span>
                  右键菜单操作
                </span>
              </div>

              <!-- 精致路径定位面包屑 -->
              <div class="mb-3 flex items-center justify-between bg-slate-50 border border-slate-200/80 rounded-xl px-3 py-2 text-xs font-medium text-slate-700 shadow-sm shrink-0">
                <div class="flex items-center gap-1.5 truncate">
                  <svg class="w-3.5 h-3.5 text-slate-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                  </svg>
                  <span class="text-slate-400 text-[10px] uppercase font-bold tracking-wider">定位目录:</span>
                  <span class="font-mono text-[11px] text-slate-600 truncate">
                    {{ selectedDirectoryPath || '技能根目录' }}
                  </span>
                </div>
                
                <button
                  v-if="selectedDirectoryPath !== ''"
                  @click="selectedDirectoryPath = ''"
                  class="text-slate-400 hover:text-slate-600 p-0.5 rounded-md hover:bg-slate-200/60 transition-colors"
                  title="清除定位并定位回根目录"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <!-- 文件树渲染 -->
              <div @contextmenu.self.prevent="handleEmptyAreaContextMenu" class="flex-1 flex flex-col border border-gray-200 rounded-2xl p-4 bg-gray-50/50 overflow-y-auto min-h-[320px] custom-scrollbar mb-4">
                <div v-if="fileTree.length === 0" class="text-center py-10 text-xs text-gray-400 italic flex-1 flex items-center justify-center">
                  暂无任何技能物理资产文件
                </div>
                <SkillFileTree 
                  v-else
                  :tree-data="fileTree" 
                  :selected-path="selectedFilePath"
                  :selected-directory-path="selectedDirectoryPath"
                  @select-file="selectFileForEdit"
                  @select-directory="selectDirectory"
                  @delete-file="deleteSkillFile"
                  @context-menu="handleContextMenu"
                />
              </div>

            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 新建技能资产 Modal -->
  <div
    v-if="showCreateAssetModal"
    class="fixed inset-0 bg-black/45 backdrop-blur-sm z-[9995] flex items-center justify-center p-4 animate-fade-in"
    @click.self="showCreateAssetModal = false"
  >
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-5 border border-gray-150">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-bold text-gray-800">
          新建{{ createAssetType === 'file' ? '文件' : '文件夹' }}
        </h2>
        <button
          @click="showCreateAssetModal = false"
          class="p-1 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
          aria-label="关闭"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="mb-4 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg">
        <p class="text-[10px] text-gray-400 font-bold mb-1">创建位置</p>
        <p class="text-xs text-slate-600 font-mono break-all">{{ createAssetTargetLabel }}</p>
      </div>

      <label class="block text-xs font-bold text-gray-600 mb-1.5">
        {{ createAssetType === 'file' ? '文件名（需包含文本扩展名）' : '文件夹名称' }}
      </label>
      <input
        v-model="createAssetName"
        type="text"
        :placeholder="createAssetType === 'file' ? '例如: guide.md' : '例如: references'"
        class="w-full px-3 py-2 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm font-mono"
        @keyup.enter="createSkillAsset"
      />
      <p class="mt-1.5 text-[10px] text-gray-400">请输入单级名称，不支持 /、\ 或隐藏名称。</p>

      <div class="flex justify-end gap-2 mt-5 pt-4 border-t border-gray-100">
        <button
          @click="showCreateAssetModal = false"
          class="px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          取消
        </button>
        <button
          @click="createSkillAsset"
          :disabled="creatingAsset"
          class="px-3 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg"
        >
          {{ creatingAsset ? '创建中...' : '确认创建' }}
        </button>
      </div>
    </div>
  </div>

  <!-- 导入技能包 Modal -->
  <div
    v-if="showImportModal"
    class="fixed inset-0 bg-black/45 backdrop-blur-sm z-[9995] flex items-center justify-center p-4 animate-fade-in"
    @click.self="showImportModal = false"
  >
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6 border border-gray-150 relative">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-bold text-gray-800 flex items-center gap-1.5">
          <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          导入智能体技能压缩包
        </h2>
        <button
          @click="showImportModal = false"
          class="p-1 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
          aria-label="关闭"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- 拖拽上传区域 -->
      <div
        @dragover="handleImportDragOver"
        @dragleave="handleImportDragLeave"
        @drop="handleImportDrop"
        @click="() => $refs.importFileInput.click()"
        class="border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all flex flex-col items-center justify-center"
        :class="[
          importDragActive 
            ? 'border-blue-500 bg-blue-50/50' 
            : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
        ]"
      >
        <input
          type="file"
          ref="importFileInput"
          accept=".zip,.tar,.gz,.tgz,.bz2"
          @change="handleImportFileChange"
          class="hidden"
        />
        
        <template v-if="!importFile">
          <svg class="w-10 h-10 text-gray-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <span class="text-xs text-gray-600 font-bold">拖拽压缩包至此，或点击选择文件</span>
          <span class="text-[10px] text-gray-400 mt-1.5">仅支持 .zip, .tar, .tar.gz, .tgz 等压缩文件，最大 20MB</span>
        </template>
        
        <template v-else>
          <svg class="w-10 h-10 text-blue-500 mb-3 animate-bounce" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span class="text-xs text-gray-800 font-bold truncate max-w-full px-4">{{ importFile.name }}</span>
          <span class="text-[10px] text-gray-500 mt-1">{{ (importFile.size / 1024 / 1024).toFixed(2) }} MB</span>
          <span class="text-[10px] text-blue-600 mt-2 hover:underline">重新选择文件</span>
        </template>
      </div>

      <!-- 选项配置 -->
      <div class="mt-4 flex items-center space-x-2 bg-slate-50 border border-slate-200 rounded-xl p-3 select-none">
        <input
          type="checkbox"
          id="import-overwrite-checkbox"
          v-model="importOverwrite"
          class="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 cursor-pointer"
        />
        <label for="import-overwrite-checkbox" class="text-xs font-semibold text-gray-700 cursor-pointer">
          覆盖已存在的同名技能 (Overwrite Existing)
        </label>
      </div>
      <p class="mt-1.5 text-[10px] text-gray-400">若技能包解压后根目录不含 <b>SKILL.md</b> 规范文档，系统将自动拒绝并回滚清理。</p>

      <!-- 操作按钮 -->
      <div class="flex justify-end gap-2 mt-5 pt-4 border-t border-gray-100">
        <button
          @click="showImportModal = false"
          class="px-4 py-2 text-xs font-semibold text-gray-600 border border-gray-300 rounded-xl hover:bg-gray-50"
        >
          取消
        </button>
        <button
          @click="submitImportSkill"
          :disabled="importingSkill || !importFile"
          class="px-4 py-2 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:hover:bg-blue-600 rounded-xl flex items-center gap-1.5 transition-all shadow-sm"
        >
          <span v-if="importingSkill" class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
          {{ importingSkill ? '正在导入解压...' : '确认导入' }}
        </button>
      </div>
    </div>
  </div>

  <ConfirmModal
    v-if="confirmState.show"
    :title="confirmState.title"
    :message="confirmState.message"
    :type="confirmState.type"
    @confirm="confirmState.onConfirm"
    @cancel="confirmState.show = false"
  />

  <!-- 统计查看 Modal -->
  <div 
    v-if="showStatsModal" 
    class="fixed inset-0 bg-black/50 backdrop-blur-sm z-[9990] flex items-center justify-center p-4 animate-fade-in"
    @click.self="closeStatsModal"
  >
    <div class="bg-white rounded-2xl shadow-2xl max-w-4xl w-full p-6 border border-gray-150 flex flex-col max-h-[90vh]">
      <div class="flex items-center justify-between border-b border-gray-100 pb-4 mb-4 select-none shrink-0">
        <h2 class="text-xl font-bold text-gray-800 flex items-center gap-2">
          <svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          技能激活与调用统计
        </h2>
        <button @click="closeStatsModal" class="text-gray-400 hover:text-gray-600 transition-colors">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div v-if="loadingStats" class="flex flex-col items-center justify-center py-20 flex-1">
        <div class="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
        <p class="text-sm text-gray-500 mt-4 font-medium">正在拉取统计数据...</p>
      </div>
      <div v-else-if="!hasStatsData" class="flex flex-col items-center justify-center py-20 flex-1 text-gray-400 select-none">
        <svg class="w-16 h-16 text-gray-300 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0a2 2 0 01-2 2H6a2 2 0 01-2-2m16 0V9a2 2 0 00-2-2H6a2 2 0 00-2 2v4m16 0a2 2 0 01-2 2H6a2 2 0 01-2-2" />
        </svg>
        <p class="text-sm font-medium">暂无任何技能的激活和调用数据</p>
        <p class="text-xs text-gray-400 mt-1">在智能体对话中触发技能后，统计系统会自动开始记录</p>
      </div>
      <div v-else class="flex-1 flex flex-col min-h-[400px] overflow-hidden">
        <!-- Tab 切换菜单 -->
        <div class="flex border-b border-gray-200 mb-4 shrink-0 select-none gap-2">
          <button 
            @click="switchTab('distribution')"
            class="px-5 py-2.5 font-bold text-sm transition-all border-b-2 -mb-[2px]"
            :class="activeTab === 'distribution' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-400 hover:text-gray-600'"
          >
            总量占比与分布
          </button>
          <button 
            @click="switchTab('trend')"
            class="px-5 py-2.5 font-bold text-sm transition-all border-b-2 -mb-[2px]"
            :class="activeTab === 'trend' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-400 hover:text-gray-600'"
          >
            近30天激活趋势
          </button>
        </div>

        <div class="flex-1 overflow-y-auto pr-1 py-1 custom-scrollbar">
          <!-- 分布图 -->
          <div v-show="activeTab === 'distribution'" class="border border-gray-150 rounded-xl p-4 bg-gray-50/50 flex flex-col min-h-[380px]">
            <h3 class="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5 shrink-0 select-none">
              <span class="w-2 h-2 bg-blue-500 rounded-full"></span>
              技能总调用占比与分布
            </h3>
            <div ref="distributionChartRef" class="w-full flex-1 min-h-[320px]"></div>
          </div>
          <!-- 趋势图 -->
          <div v-show="activeTab === 'trend'" class="border border-gray-150 rounded-xl p-4 bg-gray-50/50 flex flex-col min-h-[380px]">
            <h3 class="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5 shrink-0 select-none">
              <span class="w-2 h-2 bg-indigo-500 rounded-full"></span>
              近30天激活趋势变化
            </h3>
            <div ref="trendChartRef" class="w-full flex-1 min-h-[320px]"></div>
          </div>
        </div>
      </div>
      
      <div class="flex justify-end gap-3 mt-6 border-t border-gray-100 pt-4 shrink-0">
        <button 
          @click="closeStatsModal"
          class="px-5 py-2.5 bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors text-sm font-semibold rounded-xl"
        >
          关闭
        </button>
      </div>
    </div>
  </div>

  <!-- 右键菜单 -->
  <div
    v-if="contextMenu.show"
    :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
    class="fixed z-[9999] bg-white border border-gray-200 rounded-lg shadow-lg py-1 w-48 text-xs text-gray-700 select-none animate-fade-in"
  >
    <!-- 文件夹专属操作 -->
    <template v-if="contextMenu.node?.is_dir">
      <button
        @click="handleMenuAction('new-file')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors text-blue-600 font-medium"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        新建文件
      </button>
      <button
        @click="handleMenuAction('new-folder')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors text-amber-700 font-medium"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
        </svg>
        新建文件夹
      </button>
      <div class="border-t border-gray-100 my-1"></div>
      <button
        @click="handleMenuAction('upload-normal')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
      >
        <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
        </svg>
        上传普通文件
      </button>
      <button
        @click="handleMenuAction('upload-archive')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
      >
        <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
        </svg>
        上传压缩包 (Zip/Tar)
      </button>
      <div v-if="contextMenu.node?.path !== ''" class="border-t border-gray-100 my-1"></div>
      <button
        v-if="contextMenu.node?.path !== ''"
        @click="handleMenuAction('delete')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 text-red-500 hover:bg-red-50 transition-colors"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
        删除此文件夹
      </button>
    </template>

    <!-- 普通文件专属操作 -->
    <template v-else>
      <button
        @click="handleMenuAction('edit')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors font-medium text-slate-800"
      >
        <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
        </svg>
        编辑此文件
      </button>
      <template v-if="contextMenu.node?.name.toLowerCase() !== 'skill.md'">
        <div class="border-t border-gray-100 my-1"></div>
        <button
          @click="handleMenuAction('delete')"
          class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 text-red-500 hover:bg-red-50 transition-colors"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          删除此文件
        </button>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* 渐变流光与动画 */
@keyframes slideIn {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

.animate-slide-in {
  animation: slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.animate-fade-in {
  animation: fadeIn 0.2s ease-out forwards;
}

/* 自定义滚动条 */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* Hide focus ring */
textarea:focus {
  outline: none;
  box-shadow: none;
}

/* Skill Editor (light theme) */
.skill-editor {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px rgba(15, 23, 42, 0.04);
}

.skill-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.skill-editor-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.skill-editor-file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  flex-shrink: 0;
  background: #eff6ff;
  color: #2563eb;
}

.skill-editor-file-icon.ext-md {
  background: #eff6ff;
  color: #2563eb;
}
.skill-editor-file-icon.ext-sh,
.skill-editor-file-icon.ext-bash {
  background: #f0fdf4;
  color: #16a34a;
}
.skill-editor-file-icon.ext-py {
  background: #fefce8;
  color: #ca8a04;
}
.skill-editor-file-icon.ext-json {
  background: #fff7ed;
  color: #ea580c;
}

.skill-editor-filename {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
  color: #334155;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-editor-unsaved {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 1px 7px;
  border-radius: 999px;
}

.skill-editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.skill-editor-mode-toggle {
  display: flex;
  align-items: center;
  padding: 2px;
  border-radius: 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
}

.skill-editor-mode-toggle button {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  transition: all 0.15s ease;
}

.skill-editor-mode-toggle button:hover {
  color: #334155;
}

.skill-editor-mode-toggle button.active {
  background: #ffffff;
  color: #1e293b;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.skill-editor-save-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  transition: all 0.15s ease;
  cursor: not-allowed;
}

.skill-editor-save-btn.is-dirty {
  color: #fff;
  background: #2563eb;
  border-color: #3b82f6;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(37, 99, 235, 0.25);
}

.skill-editor-save-btn.is-dirty:hover {
  background: #1d4ed8;
}

.skill-editor-save-btn.is-dirty:active {
  transform: scale(0.97);
}

.skill-editor-save-btn.is-saving {
  cursor: wait;
  opacity: 0.8;
}

.skill-editor-save-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.skill-editor-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
}

.skill-editor-code-pane {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.skill-editor-gutter {
  width: 44px;
  flex-shrink: 0;
  padding: 12px 8px 12px 0;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.625rem;
  color: #94a3b8;
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  overflow: hidden;
  user-select: none;
}

.skill-editor-line-num {
  height: 1.625rem;
}

.skill-editor-textarea {
  flex: 1;
  padding: 12px 16px;
  background: #ffffff;
  color: #1e293b;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.625rem;
  border: none;
  resize: none;
  overflow-y: auto;
  caret-color: #2563eb;
}

.skill-editor-textarea::placeholder {
  color: #cbd5e1;
}

.skill-editor-textarea::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.skill-editor-textarea::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.skill-editor-textarea::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.skill-editor-preview {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  color: #1e293b;
  background: #ffffff;
}

.skill-editor-preview::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.skill-editor-preview::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.skill-editor-preview::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.skill-editor-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 14px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10px;
  color: #94a3b8;
  flex-shrink: 0;
  user-select: none;
}

.skill-editor-status-left,
.skill-editor-status-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.skill-editor-status-sep {
  opacity: 0.6;
}

.skill-editor-status-dirty {
  color: #d97706;
}

/* 生态市场外链按钮特殊强制渐变背景以防止被第三方库覆盖 */
.market-link {
  background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
  color: #ffffff !important;
}
.market-link:hover {
  background: linear-gradient(135deg, #1d4ed8, #4338ca) !important;
  color: #ffffff !important;
}

/* Markdown Preview Styling (light theme) */
.markdown-preview :deep(h1) {
  font-size: 1.625rem;
  font-weight: 700;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 0.5rem;
  margin-top: 0;
  margin-bottom: 1rem;
  color: #0f172a;
  letter-spacing: -0.01em;
}
.markdown-preview :deep(h2) {
  font-size: 1.25rem;
  font-weight: 600;
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  color: #1e293b;
}
.markdown-preview :deep(h3) {
  font-size: 1.05rem;
  font-weight: 600;
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  color: #334155;
}
.markdown-preview :deep(p) {
  margin-bottom: 0.85rem;
  line-height: 1.75;
  color: #475569;
}
.markdown-preview :deep(ul), .markdown-preview :deep(ol) {
  margin-left: 1.5rem;
  margin-bottom: 1rem;
  list-style-type: disc;
}
.markdown-preview :deep(ol) {
  list-style-type: decimal;
}
.markdown-preview :deep(li) {
  margin-bottom: 0.35rem;
  line-height: 1.65;
  color: #475569;
}
.markdown-preview :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.875em;
  background-color: #f1f5f9;
  padding: 0.15rem 0.4rem;
  border-radius: 0.25rem;
  color: #be185d;
  border: 1px solid #e2e8f0;
}
.markdown-preview :deep(pre) {
  background-color: #f8fafc;
  padding: 1rem 1.25rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin-bottom: 1rem;
  border: 1px solid #e2e8f0;
}
.markdown-preview :deep(pre code) {
  background-color: transparent;
  padding: 0;
  border: none;
  color: #334155;
  font-size: 0.8125rem;
  line-height: 1.6;
}
.markdown-preview :deep(blockquote) {
  border-left: 3px solid #3b82f6;
  padding: 0.25rem 0 0.25rem 1rem;
  margin-left: 0;
  margin-right: 0;
  margin-bottom: 1rem;
  color: #64748b;
  background: #f8fafc;
  border-radius: 0 0.375rem 0.375rem 0;
}
.markdown-preview :deep(a) {
  color: #2563eb;
  text-decoration: none;
}
.markdown-preview :deep(a:hover) {
  text-decoration: underline;
}
.markdown-preview :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 1.5rem 0;
}
.markdown-preview :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1rem;
  font-size: 0.875rem;
}
.markdown-preview :deep(th),
.markdown-preview :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 0.5rem 0.75rem;
  text-align: left;
}
.markdown-preview :deep(th) {
  background: #f8fafc;
  font-weight: 600;
  color: #334155;
}
.markdown-preview :deep(.preview-empty) {
  color: #94a3b8;
  font-style: italic;
  padding: 1rem 0;
}
</style>
