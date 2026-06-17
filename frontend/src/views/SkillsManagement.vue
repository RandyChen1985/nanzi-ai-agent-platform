<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import axios from '../utils/axios'
import { useToast } from '../composables/useToast'
import SkillFileTree from '../components/SkillFileTree.vue'

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

// 拖拽上传相关
const dragActive = ref(false)
const uploadFolder = ref('') // 上传到技能目录下的子文件夹路径 (可选)
const uploading = ref(false)

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
const deleteSkill = async (skillId: string) => {
  if (!confirm(`确定要彻底删除技能 [${skillId}] 吗？\n该操作会物理删除对应的技能目录及其所有子资产脚本，且不可恢复！`)) {
    return
  }

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
  }
}

// 查看技能详情与抽屉挂载
const openSkillDetail = async (skillId: string) => {
  activeSkillId.value = skillId
  selectedFilePath.value = 'SKILL.md'
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
  if (hasUnsavedChanges.value) {
    if (!confirm('当前文件有未保存的修改，切换文件将丢失这些修改。确定要切换吗？')) {
      return
    }
  }
  selectedFilePath.value = path
  if (path === 'SKILL.md') {
    await fetchSkillDetail(activeSkillId.value)
  } else {
    await loadFileContent(activeSkillId.value, path)
  }
}

// 是否有未保存的修改
const hasUnsavedChanges = computed(() => {
  return editingContent.value !== originalContent.value
})

// 计算行数以显示在 IDE 风格编辑器中
const lineCount = computed(() => {
  return editingContent.value.split('\n').length || 1
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
const deleteSkillFile = async (filePath: string) => {
  if (!confirm(`确定要删除物理资产 [${filePath}] 吗？\n该删除操作无法撤销！`)) {
    return
  }

  try {
    const response = await axios.delete(`/api/portal/skills/${activeSkillId.value}/files`, {
      params: { path: filePath }
    })
    if (response.data && response.data.status === 'success') {
      showToast('资产已成功物理删除', 'success')
      if (selectedFilePath.value === filePath) {
        selectedFilePath.value = 'SKILL.md'
      }
      await fetchSkillDetail(activeSkillId.value)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '删除资产失败', 'error')
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

// 物理上传执行 (单文件限 10MB)
const uploadFiles = async (files: FileList) => {
  uploading.value = true
  try {
    for (const file of Array.from(files)) {
      if (file.size > 10 * 1024 * 1024) {
        showToast(`文件 ${file.name} 超过 10MB 大小限制，已拦截上传`, 'warning')
        continue
      }

      const formData = new FormData()
      formData.append('file', file)
      if (uploadFolder.value.trim()) {
        formData.append('folder', uploadFolder.value.trim())
      }

      await axios.post(`/api/portal/skills/${activeSkillId.value}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })
      showToast(`文件 ${file.name} 上传成功！`, 'success')
    }
    // 重置上传目录并重新获取详情更新文件树
    uploadFolder.value = ''
    await fetchSkillDetail(activeSkillId.value)
  } catch (e: any) {
    showToast(e.response?.data?.detail || '文件上传遇到错误', 'error')
  } finally {
    uploading.value = false
  }
}

onMounted(() => {
  fetchSkills()
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
            <!-- 左侧：在线编辑器 (占据主屏 65%) -->
            <div class="flex-[1.8] flex flex-col border-r border-gray-150 p-6 bg-slate-50 overflow-y-auto">
              <div class="flex items-center justify-between mb-4 flex-shrink-0">
                <div class="flex items-center space-x-2">
                  <span class="text-xs font-bold text-gray-500 uppercase tracking-wider">正在编辑：</span>
                  <span class="px-2.5 py-1 bg-slate-200 text-slate-700 font-mono text-xs rounded-md border border-slate-300 font-bold">
                    {{ selectedFilePath }}
                  </span>
                </div>
                
                <!-- 保存更改按钮 -->
                <button 
                  @click="saveFileContent"
                  :disabled="saving || !hasUnsavedChanges"
                  class="flex items-center gap-1.5 px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:hover:bg-emerald-600 text-white rounded-lg transition-all text-xs font-bold shadow-sm"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
                  </svg>
                  保存更改
                </button>
              </div>

              <!-- 编辑器主体 -->
              <div v-if="fetchingDetail" class="flex-1 flex flex-col items-center justify-center py-20">
                <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                <p class="text-xs text-gray-400 mt-3 font-medium">载入文件内容...</p>
              </div>

              <div v-else class="flex-1 flex flex-col bg-slate-900 rounded-xl overflow-hidden border border-slate-800 shadow-xl relative min-h-[400px]">
                <!-- IDE-Like Tab Bar -->
                <div class="flex items-center justify-between px-4 py-2 bg-slate-950 border-b border-slate-900 flex-shrink-0 select-none">
                  <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-red-500"></span>
                    <span class="w-2.5 h-2.5 rounded-full bg-yellow-500"></span>
                    <span class="w-2.5 h-2.5 rounded-full bg-green-500"></span>
                    <span class="text-[11px] text-slate-400 font-mono pl-2 truncate max-w-md">{{ selectedFilePath }}</span>
                  </div>
                  <div class="flex items-center space-x-2 text-[10px] text-slate-500 font-mono">
                    <span v-if="hasUnsavedChanges" class="w-2 h-2 rounded-full bg-amber-500 animate-pulse" title="文件已被修改"></span>
                    <span>UTF-8</span>
                  </div>
                </div>

                <!-- Textarea With Line Numbers -->
                <div class="flex-1 flex overflow-hidden min-h-0 bg-slate-950/80">
                  <div class="w-10 bg-slate-950/20 text-slate-600 text-right pr-2.5 py-3 select-none font-mono text-[10px] sm:text-xs border-r border-slate-900 leading-6 overflow-hidden">
                    <div v-for="n in lineCount" :key="n">{{ n }}</div>
                  </div>
                  <textarea 
                    v-model="editingContent"
                    class="flex-1 p-3 bg-transparent text-slate-200 font-mono text-xs focus:outline-none resize-none leading-6 overflow-y-auto"
                    placeholder="在此输入文本内容..."
                  ></textarea>
                </div>
              </div>
            </div>

            <!-- 右侧：文件资产与脚本上传 (占据 35%) -->
            <div class="flex-1 flex flex-col p-6 overflow-y-auto">
              <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">技能资产物理树</h3>

              <!-- 文件树渲染 -->
              <div class="flex-1 border border-gray-200 rounded-xl p-3 bg-gray-50/50 overflow-y-auto max-h-[360px] custom-scrollbar mb-6">
                <div v-if="fileTree.length === 0" class="text-center py-6 text-xs text-gray-400 italic">
                  暂无任何技能物理资产文件
                </div>
                <SkillFileTree 
                  v-else
                  :tree-data="fileTree" 
                  :selected-path="selectedFilePath"
                  @select-file="selectFileForEdit"
                  @delete-file="deleteSkillFile"
                />
              </div>

              <!-- 上传控件区域 -->
              <div class="flex-shrink-0 border-t border-gray-150 pt-5 space-y-4">
                <h4 class="text-xs font-bold text-gray-700 flex items-center gap-1.5">
                  <svg class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                  上传辅助代码与脚本 (最大 10MB)
                </h4>
                
                <div>
                  <label class="block text-[10px] text-gray-400 mb-1 font-bold">目标子文件夹路径 (可选，默认为技能根目录)</label>
                  <input 
                    v-model="uploadFolder"
                    type="text" 
                    placeholder="例如: scripts" 
                    class="w-full px-2.5 py-1.5 bg-white border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-500 text-xs font-mono"
                  />
                </div>

                <!-- 拖拽上传板 -->
                <div 
                  @dragover="handleDragOver"
                  @dragleave="handleDragLeave"
                  @drop="handleDrop"
                  class="border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center"
                  :class="[
                    dragActive 
                      ? 'border-blue-500 bg-blue-50/50' 
                      : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
                  ]"
                >
                  <input 
                    type="file" 
                    id="file-upload-input" 
                    multiple 
                    @change="handleFileUpload" 
                    class="hidden"
                  />
                  <label for="file-upload-input" class="cursor-pointer w-full flex flex-col items-center">
                    <svg class="w-8 h-8 text-gray-400 group-hover:text-blue-500 transition-colors mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                    <span class="text-xs text-gray-600 font-bold">
                      {{ uploading ? '正在物理传输中...' : '拖拽文件至此，或点击上传' }}
                    </span>
                    <span class="text-[10px] text-gray-400 mt-1">支持 Python, JS 等扩展脚本文件</span>
                  </label>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
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

/* 生态市场外链按钮特殊强制渐变背景以防止被第三方库覆盖 */
.market-link {
  background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
  color: #ffffff !important;
}
.market-link:hover {
  background: linear-gradient(135deg, #1d4ed8, #4338ca) !important;
  color: #ffffff !important;
}
</style>
