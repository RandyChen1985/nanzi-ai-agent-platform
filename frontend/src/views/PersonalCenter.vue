<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from '../utils/axios'
import Toast from '../components/Toast.vue'
import { useBranding } from '../composables/useBranding'
import { renderMarkdown } from '../utils/markdown'
import { copyToClipboard } from '../utils/clipboard'

const { branding, loadBranding } = useBranding()

const userInfo = ref<any>({})
const userApiKey = ref('')
const loadingApiKey = ref(false)
const apiKeyRevealed = ref(false)
const newPassword = ref('')
const confirmPassword = ref('')
const loadingPassword = ref(false)

const apiKeyDisplay = computed(() => {
  if (!userApiKey.value) return '点击“查看”加载'
  if (!apiKeyRevealed.value) {
    return '•'.repeat(Math.min(28, Math.max(16, userApiKey.value.length)))
  }
  return userApiKey.value
})

const toast = ref({
  show: false,
  message: '',
  type: 'info' as 'success' | 'error' | 'warning' | 'info',
  key: 0
})

const showToast = (message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') => {
  toast.value = {
    show: true,
    message,
    type,
    key: toast.value.key + 1
  }
}

const closeToast = () => {
  toast.value.show = false
}

const fetchUserInfo = async () => {
    try {
        const response = await axios.get('/api/portal/auth/me')
        if (response.data && response.data.status === 'success') {
            userInfo.value = response.data.data
        }
    } catch (e) {
        console.error("Failed to fetch user info", e)
    }
}

const fetchApiKey = async () => {
  if (!userInfo.value.user_id) return
  
  loadingApiKey.value = true
  try {
    const response = await axios.get(`/api/portal/management/api-key/${userInfo.value.user_id}`)
    userApiKey.value = response.data.api_key
    apiKeyRevealed.value = true
  } catch (error: any) {
    showToast(error.response?.data?.detail || '获取 API Key 失败', 'error')
  } finally {
    loadingApiKey.value = false
  }
}

const hideApiKey = () => {
  apiKeyRevealed.value = false
}

const revealOrFetchApiKey = async () => {
  if (userApiKey.value) {
    apiKeyRevealed.value = true
    return
  }
  await fetchApiKey()
}

const copyApiKey = async () => {
  if (!userApiKey.value) return
  const success = await copyToClipboard(userApiKey.value)
  if (success) {
    showToast('API Key 已复制', 'success')
  } else {
    showToast('复制失败，请手动复制', 'error')
  }
}

const handlePasswordChange = async () => {
    if (!newPassword.value || newPassword.value.length < 6) {
        showToast('密码长度至少需要6位', 'warning')
        return
    }
    if (newPassword.value !== confirmPassword.value) {
        showToast('两次输入的密码不一致', 'warning')
        return
    }
    
    loadingPassword.value = true
    try {
        const response = await axios.put('/api/portal/auth/password', {
            password: newPassword.value
        })
        if (response.data && response.data.status === 'success') {
            showToast('密码修改成功', 'success')
            newPassword.value = ''
            confirmPassword.value = ''
        } else {
            showToast('修改失败', 'error')
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '修改失败', 'error')
    } finally {
        loadingPassword.value = false
    }
}

import { watch } from 'vue'
import Modal from '../components/Modal.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import PersonalTokenUsage from '../components/personal/PersonalTokenUsage.vue'
import NotificationConfigs from '../components/personal/NotificationConfigs.vue'
import DataPortalHome from './DataPortalHome.vue'
import SkillsManagement from './SkillsManagement.vue'
import { useRoute, useRouter } from 'vue-router'

type PersonalTab = 'info' | 'permissions' | 'memory' | 'tokens' | 'notifications' | 'data' | 'skills'
const route = useRoute()
const router = useRouter()
const personalTabs: PersonalTab[] = ['info', 'permissions', 'memory', 'tokens', 'notifications', 'data', 'skills']
const activeTab = ref<PersonalTab>(personalTabs.includes(route.query.tab as PersonalTab) ? route.query.tab as PersonalTab : 'info')
const skillsInitialId = computed(() => String(route.query.skill_id || '').trim())
const permissionsSubTab = ref<'list' | 'about'>('list')
const showAboutTab = computed(() => !!branding.value.contact_markdown?.trim())
const contactHtml = computed(() => renderMarkdown(branding.value.contact_markdown || ''))
const loadingPermissions = ref(false)
const permissions = ref<{
    roles?: string[],
    permissions?: {
        agents?: string[],
        datasets?: string[],
        apis?: string[],
        metadata?: string[],
        menus?: string[],
        elements?: string[]
    },
    details?: {
        agents?: Array<{ id: string, name: string, display_name?: string, description?: string }>,
        datasets?: Array<{ id: string, name: string, display_name?: string, description?: string }>,
        apis?: Array<{ id: string, name: string, display_name?: string, description?: string }>,
        metadata?: Array<{ id: string, name: string, display_name?: string, description?: string }>,
        menus?: Array<{ id: string, name: string, display_name?: string, description?: string }>,
        elements?: Array<{ id: string, name: string, display_name?: string, description?: string }>
    }
}>({})

const fetchPermissions = async () => {
    loadingPermissions.value = true
    try {
        const permRes = await axios.get('/api/portal/auth/permissions')
        permissions.value = permRes.data || {}
    } catch (e) {
        console.error("Failed to fetch permissions", e)
        showToast('获取权限列表失败', 'error')
    } finally {
        loadingPermissions.value = false
    }
}

// 我的记忆相关数据状态与方法
/** 单层视图：每日摘要 | 会话摘要 | 长期记忆 */
const memoryView = ref<'daily' | 'session' | 'ltm'>('daily')
const showMemoryDateFilter = ref(false)
const memoryMenuKey = ref<string | null>(null)

// 每日摘要数据与状态
const myDailySummaries = ref<any[]>([])
const dailyLoading = ref(false)
const dailyKeyword = ref('')
const dailyDateFrom = ref('')
const dailyDateTo = ref('')

const showDailyDetailModal = ref(false)
const dailyDetailLoading = ref(false)
const dailyDetail = ref<any>(null)
const dailySessions = ref<any[]>([])

const showRebuildDailyConfirm = ref(false)
const dailyToRebuild = ref<any>(null)

const showDeleteDailyConfirm = ref(false)
const dailyToDelete = ref<any>(null)

// 会话记忆数据与状态
const mySessions = ref<any[]>([])
const sessionsLoading = ref(false)
const sessionsKeyword = ref('')

// 会话详情
const showSessionDetailModal = ref(false)
const sessionDetailLoading = ref(false)
const sessionDetail = ref<any>(null)
const sessionHistory = ref<any[]>([])

// 删除会话记忆
const showDeleteSessionConfirm = ref(false)
const sessionToDelete = ref<any>(null)

const showClearAllSessionMemoryConfirm = ref(false)
const clearingAllSessionMemory = ref(false)

// 长期记忆 (LTM)
const myLtm = ref<Record<string, string>>({})
const ltmLoading = ref(false)

// LTM 弹框表单
const showLtmModal = ref(false)
const ltmForm = ref({
    key: '',
    value: '',
    isEdit: false,
    originalKey: ''
})

// 删除 LTM 记忆项
const showDeleteLtmConfirm = ref(false)
const ltmKeyToDelete = ref('')

const formatTime = (ts?: number) => {
    if (!ts) return '-'
    return new Date(ts * 1000).toLocaleString()
}

const formatList = (value?: string | string[]) => {
    if (!value) return []
    if (Array.isArray(value)) return value.filter(Boolean)
    try {
        const parsed = JSON.parse(value)
        if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean)
    } catch (_) {}
    return String(value).trim() ? [String(value)] : []
}

const toggleMemoryMenu = (key: string, event?: Event) => {
    event?.stopPropagation()
    memoryMenuKey.value = memoryMenuKey.value === key ? null : key
}

const closeMemoryMenu = () => {
    memoryMenuKey.value = null
}

// 获取每日摘要列表
const fetchMyDailySummaries = async () => {
    dailyLoading.value = true
    try {
        const response = await axios.get('/api/portal/memory/my/daily-summaries', {
            params: {
                keyword: dailyKeyword.value.trim() || undefined,
                date_from: dailyDateFrom.value || undefined,
                date_to: dailyDateTo.value || undefined
            }
        })
        if (response.data && response.data.status === 'success') {
            myDailySummaries.value = response.data.data || []
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '拉取每日摘要失败', 'error')
    } finally {
        dailyLoading.value = false
    }
}

// 获取每日摘要详情
const openDailyDetail = async (row: any) => {
    showDailyDetailModal.value = true
    dailyDetailLoading.value = true
    dailyDetail.value = row
    dailySessions.value = []
    try {
        const response = await axios.get(`/api/portal/memory/my/daily-summaries/${row.date}`)
        if (response.data && response.data.status === 'success') {
            const data = response.data.data
            dailyDetail.value = data.summary || row
            dailySessions.value = data.sessions || []
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '加载每日摘要详情失败', 'error')
        showDailyDetailModal.value = false
    } finally {
        dailyDetailLoading.value = false
    }
}

// 重建每日摘要
const confirmRebuildDaily = (row: any) => {
    dailyToRebuild.value = row
    showRebuildDailyConfirm.value = true
}

const executeRebuildDaily = async () => {
    if (!dailyToRebuild.value) return
    const day = dailyToRebuild.value.date
    showRebuildDailyConfirm.value = false
    try {
        const response = await axios.post(`/api/portal/memory/my/daily-summaries/${day}/rebuild`)
        if (response.data && response.data.status === 'success') {
            showToast('每日摘要已重建', 'success')
            await fetchMyDailySummaries()
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '重建失败', 'error')
    } finally {
        dailyToRebuild.value = null
    }
}

// 删除每日摘要
const confirmDeleteDaily = (row: any) => {
    dailyToDelete.value = row
    showDeleteDailyConfirm.value = true
}

const executeDeleteDaily = async () => {
    if (!dailyToDelete.value) return
    const day = dailyToDelete.value.date
    showDeleteDailyConfirm.value = false
    try {
        const response = await axios.delete(`/api/portal/memory/my/daily-summaries/${day}`)
        if (response.data && response.data.status === 'success') {
            showToast('每日摘要已删除', 'success')
            if (showDailyDetailModal.value && dailyDetail.value?.date === day) {
                showDailyDetailModal.value = false
            }
            await fetchMyDailySummaries()
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '删除失败', 'error')
    } finally {
        dailyToDelete.value = null
    }
}

// 切换记忆视图
const switchMemoryView = (view: 'daily' | 'session' | 'ltm') => {
    if (memoryView.value === view) return
    memoryView.value = view
    memoryMenuKey.value = null
}

// 获取会话记忆列表
const fetchMySessions = async () => {
    sessionsLoading.value = true
    try {
        const response = await axios.get('/api/portal/memory/my/summaries', {
            params: {
                keyword: sessionsKeyword.value.trim() || undefined
            }
        })
        if (response.data && response.data.status === 'success') {
            mySessions.value = response.data.data || []
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '拉取会话记忆失败', 'error')
    } finally {
        sessionsLoading.value = false
    }
}

// 获取会话详情
const openSessionDetail = async (session: any) => {
    showSessionDetailModal.value = true
    sessionDetailLoading.value = true
    sessionDetail.value = session
    sessionHistory.value = []
    try {
        const response = await axios.get(`/api/portal/memory/my/summaries/${session.conversation_id}`)
        if (response.data && response.data.status === 'success') {
            const data = response.data.data
            sessionDetail.value = data.summary || session
            sessionHistory.value = data.history || []
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '获取记忆明细失败', 'error')
        showSessionDetailModal.value = false
    } finally {
        sessionDetailLoading.value = false
    }
}

// 物理删除会话记忆
const confirmDeleteSession = (session: any) => {
    sessionToDelete.value = session
    showDeleteSessionConfirm.value = true
}

const executeDeleteSession = async () => {
    if (!sessionToDelete.value) return
    const cid = sessionToDelete.value.conversation_id
    showDeleteSessionConfirm.value = false
    try {
        const response = await axios.delete(`/api/portal/memory/my/summaries/${cid}`)
        if (response.data && response.data.status === 'success') {
            showToast('会话记忆及历史聊天记录已清除', 'success')
            if (showSessionDetailModal.value && sessionDetail.value?.conversation_id === cid) {
                showSessionDetailModal.value = false
            }
            await fetchMySessions()
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '清除会话记忆失败', 'error')
    } finally {
        sessionToDelete.value = null
    }
}

const confirmClearAllSessionMemory = () => {
    showClearAllSessionMemoryConfirm.value = true
}

const executeClearAllSessionMemory = async () => {
    showClearAllSessionMemoryConfirm.value = false
    clearingAllSessionMemory.value = true
    try {
        const response = await axios.delete('/api/portal/memory/my/session-memory')
        if (response.data && response.data.status === 'success') {
            showToast('全部会话记忆已清除', 'success')
            showDailyDetailModal.value = false
            showSessionDetailModal.value = false
            if (memoryView.value === 'daily') {
                await fetchMyDailySummaries()
            } else if (memoryView.value === 'session') {
                await fetchMySessions()
            }
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '清除全部会话记忆失败', 'error')
    } finally {
        clearingAllSessionMemory.value = false
    }
}

// 长期记忆 LTM
const fetchMyLtm = async () => {
    ltmLoading.value = true
    try {
        const response = await axios.get('/api/portal/memory/my/ltm')
        if (response.data && response.data.status === 'success') {
            myLtm.value = response.data.data || {}
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '拉取长时记忆失败', 'error')
    } finally {
        ltmLoading.value = false
    }
}

const openLtmModal = (key = '', val = '') => {
    if (key) {
        ltmForm.value = {
            key,
            value: val,
            isEdit: true,
            originalKey: key
        }
    } else {
        ltmForm.value = {
            key: '',
            value: '',
            isEdit: false,
            originalKey: ''
        }
    }
    showLtmModal.value = true
}

const saveMyLtm = async () => {
    const key = ltmForm.value.key.trim()
    const value = ltmForm.value.value.trim()
    if (!key || !value) {
        showToast('键和内容均不能为空', 'warning')
        return
    }
    try {
        if (ltmForm.value.isEdit && ltmForm.value.originalKey !== key) {
            await axios.delete(`/api/portal/memory/my/ltm/${ltmForm.value.originalKey}`)
        }
        const response = await axios.put('/api/portal/memory/my/ltm', { key, value })
        if (response.data && response.data.status === 'success') {
            showToast('记忆偏好已更新', 'success')
            showLtmModal.value = false
            await fetchMyLtm()
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '保存长期记忆偏好失败', 'error')
    }
}

const confirmDeleteLtm = (key: string) => {
    ltmKeyToDelete.value = key
    showDeleteLtmConfirm.value = true
}

const executeDeleteLtm = async () => {
    const key = ltmKeyToDelete.value
    showDeleteLtmConfirm.value = false
    if (!key) return
    try {
        const response = await axios.delete(`/api/portal/memory/my/ltm/${key}`)
        if (response.data && response.data.status === 'success') {
            showToast('记忆偏好已删除', 'success')
            await fetchMyLtm()
        }
    } catch (error: any) {
        showToast(error.response?.data?.detail || '删除失败', 'error')
    } finally {
        ltmKeyToDelete.value = ''
    }
}

watch(memoryView, (val) => {
    if (val === 'daily') {
        fetchMyDailySummaries()
    } else if (val === 'session') {
        fetchMySessions()
    } else if (val === 'ltm') {
        fetchMyLtm()
    }
})

watch(activeTab, (val) => {
    const nextQuery: Record<string, any> = { ...route.query }
    if (val === 'info') {
        delete nextQuery.tab
    } else {
        nextQuery.tab = val
    }
    // 离开「我的技能」时清掉深链 skill_id，避免再切回来又自动弹出编辑抽屉
    if (val !== 'skills') {
        delete nextQuery.skill_id
    }
    router.replace({ query: nextQuery })
    if (val === 'permissions' && !permissions.value.details) {
        fetchPermissions()
    } else if (val === 'memory') {
        if (memoryView.value === 'daily') {
            fetchMyDailySummaries()
        } else if (memoryView.value === 'session') {
            fetchMySessions()
        } else {
            fetchMyLtm()
        }
    }
})

const clearSkillsDeepLink = () => {
    if (!route.query.skill_id) return
    const nextQuery: Record<string, any> = { ...route.query }
    delete nextQuery.skill_id
    router.replace({ query: nextQuery })
}
watch(() => route.query.tab, (value) => {
    activeTab.value = personalTabs.includes(value as PersonalTab) ? value as PersonalTab : 'info'
})

onMounted(() => {
    fetchUserInfo()
    loadBranding()
})
</script>

<template>
<div class="min-h-full bg-white">
    <div>
        <h2 class="px-4 pt-5 text-lg font-bold text-gray-900 sm:px-6 sm:pt-6 sm:text-xl">个人中心</h2>
        
        <!-- Tabs -->
        <div class="mt-4 border-b border-gray-200 px-4 sm:mt-6 sm:px-6">
            <nav class="-mb-px flex space-x-4 sm:space-x-8 overflow-x-auto">
                <button
                    @click="activeTab = 'info'"
                    :class="[
                        activeTab === 'info'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    基本信息
                </button>
                <button 
                    @click="activeTab = 'permissions'"
                    :class="[
                        activeTab === 'permissions'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的权限
                </button>
                <button 
                    @click="activeTab = 'memory'"
                    :class="[
                        activeTab === 'memory'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的记忆
                </button>
                <button 
                    @click="activeTab = 'tokens'"
                    :class="[
                        activeTab === 'tokens'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的 Token 消耗
                </button>
                <button
                    @click="activeTab = 'data'"
                    :class="[
                        activeTab === 'data'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的数据门户
                </button>
                <button
                    @click="activeTab = 'skills'"
                    :class="[
                        activeTab === 'skills'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的技能
                </button>
                <button 
                    @click="activeTab = 'notifications'"
                    :class="[
                        activeTab === 'notifications'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    消息通知
                </button>
            </nav>
        </div>

        <div :class="(activeTab === 'data' || activeTab === 'skills') ? '' : 'px-4 pb-4 sm:px-6 sm:pb-6'">
        <!-- Info Tab -->
        <div v-if="activeTab === 'info'">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 sm:gap-8">
                <!-- Basic Info -->
                <div class="space-y-4 sm:space-y-6">
                    <h3 class="text-md sm:text-lg font-medium text-gray-900 border-b pb-2">账号信息</h3>
                    
                    <div class="flex items-center">
                        <div class="h-12 w-12 sm:h-16 sm:w-16 rounded-full bg-primary flex items-center justify-center text-xl sm:text-2xl font-bold text-white uppercase">
                            {{ (userInfo.real_name || userInfo.user_name || 'U').substring(0, 2) }}
                        </div>
                        <div class="ml-3 sm:ml-4">
                            <p class="text-md sm:text-lg font-medium">{{ userInfo.real_name || userInfo.user_name }}</p>
                            <div class="flex flex-wrap items-center gap-2 mt-0.5 sm:mt-1">
                                <span class="text-xs sm:text-sm text-gray-500 font-mono">@{{ userInfo.user_name }}</span>
                                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] sm:text-xs font-medium" 
                                    :class="userInfo.role === 'admin' ? 'bg-purple-100 text-purple-800' : 'bg-blue-100 text-blue-800'"
                                >
                                    {{ userInfo.role === 'admin' ? '管理员' : '普通用户' }}
                                </span>
                            </div>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs sm:text-sm">
                       <div>
                           <label class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">用户ID</label>
                           <p class="font-mono text-gray-700">{{ userInfo.user_id }}</p>
                       </div>
                       <div>
                           <label class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">创建时间</label>
                           <p class="text-gray-700">{{ userInfo.created_at || '-' }}</p>
                       </div>
                       <div class="sm:col-span-2">
                           <label class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">备注</label>
                           <p class="text-gray-700 bg-gray-50 p-2 rounded border border-gray-100">{{ userInfo.remark || '暂无备注' }}</p>
                       </div>
                    </div>

                    <div>
                        <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">API Key</label>
                        <div class="flex items-center space-x-2">
                            <input
                                type="text"
                                :value="apiKeyDisplay"
                                readonly
                                class="flex-1 px-3 py-2 border border-gray-300 rounded-md bg-gray-50 text-xs sm:text-sm font-mono truncate"
                                :class="apiKeyRevealed ? 'tracking-normal' : 'tracking-widest'"
                            />
                            <button
                                v-if="!apiKeyRevealed"
                                type="button"
                                @click="revealOrFetchApiKey"
                                :disabled="loadingApiKey"
                                class="px-3 sm:px-4 py-2 bg-primary text-white text-xs sm:text-sm font-bold rounded-md hover:bg-primary-dark transition-all disabled:opacity-50 flex-shrink-0"
                            >
                                {{ loadingApiKey ? '...' : '查看' }}
                            </button>
                            <template v-else>
                                <button
                                    type="button"
                                    @click="copyApiKey"
                                    class="px-3 sm:px-4 py-2 bg-green-600 text-white text-xs sm:text-sm font-bold rounded-md hover:bg-green-700 transition-all flex-shrink-0"
                                >
                                    复制
                                </button>
                                <button
                                    type="button"
                                    @click="hideApiKey"
                                    class="px-3 sm:px-4 py-2 border border-gray-300 bg-white text-gray-600 text-xs sm:text-sm font-medium rounded-md hover:bg-gray-50 transition-all flex-shrink-0"
                                >
                                    隐藏
                                </button>
                            </template>
                        </div>
                    </div>
                </div>

                <!-- Security Settings -->
                <div class="space-y-4 sm:space-y-6">
                    <h3 class="text-md sm:text-lg font-medium text-gray-900 border-b pb-2">安全设置</h3>
                    
                    <form @submit.prevent="handlePasswordChange" class="space-y-4">
                        <div>
                            <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">新密码</label>
                            <input 
                                v-model="newPassword"
                                type="password" 
                                class="mt-1 block w-full px-3 py-2.5 bg-gray-50 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all"
                                placeholder="输入新密码 (至少6位)"
                            />
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">确认新密码</label>
                            <input 
                                v-model="confirmPassword"
                                type="password" 
                                class="mt-1 block w-full px-3 py-2.5 bg-gray-50 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all"
                                placeholder="再次输入新密码"
                            />
                        </div>

                        <div class="pt-2">
                            <button 
                                type="submit" 
                                :disabled="loadingPassword || !newPassword"
                                class="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-lg shadow-md text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-[0.98] transition-all disabled:opacity-50"
                            >
                                {{ loadingPassword ? '提交中...' : '确认修改密码' }}
                            </button>
                        </div>
                        
                        <div class="bg-amber-50 p-3 sm:p-4 rounded-lg border border-amber-100">
                            <div class="flex">
                                <div class="flex-shrink-0">
                                    <svg class="h-5 w-5 text-amber-400" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
                                    </svg>
                                </div>
                                <div class="ml-3">
                                    <h3 class="text-xs sm:text-sm font-bold text-amber-800">安全提醒</h3>
                                    <div class="mt-1 text-[11px] sm:text-xs text-amber-700 leading-relaxed">
                                        <p>修改密码后需要重新登录。此操作不会使您现有的 API Key 失效。</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- Permissions Tab -->
        <div v-else-if="activeTab === 'permissions'">
            <div v-if="loadingPermissions" class="flex justify-center py-10">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            </div>
            
            <div v-else class="space-y-4 sm:space-y-6">
                <div v-if="showAboutTab" class="border-b border-gray-200">
                    <nav class="-mb-px flex space-x-6">
                        <button
                            @click="permissionsSubTab = 'list'"
                            class="whitespace-nowrap py-3 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors"
                            :class="permissionsSubTab === 'list' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                        >
                            权限清单
                        </button>
                        <button
                            @click="permissionsSubTab = 'about'"
                            class="whitespace-nowrap py-3 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors"
                            :class="permissionsSubTab === 'about' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                        >
                            关于
                        </button>
                    </nav>
                </div>

                <div v-if="permissionsSubTab === 'about' && showAboutTab" class="bg-gray-50 border border-gray-100 rounded-xl p-4 sm:p-6">
                    <div class="markdown-body prose prose-sm max-w-none text-gray-700 break-words" v-html="contactHtml"></div>
                </div>

                <template v-else>
                <!-- Helper Text -->
                <div class="bg-gray-50 p-3 sm:p-4 rounded-lg text-xs sm:text-sm text-gray-600 flex items-start gap-3 border border-gray-100">
                    <svg class="w-5 h-5 text-gray-400 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <div>
                        <p class="font-bold text-gray-900">权限清单</p>
                        <p class="mt-1">列出您当前拥有的所有系统资源访问权限。</p>
                        <p v-if="userInfo.role === 'admin'" class="mt-2 text-purple-600 font-bold">
                            ✨ 您是超级管理员，拥有全局最高权限。
                        </p>
                        <div v-if="permissions.roles && permissions.roles.length" class="mt-2 flex flex-wrap gap-2 items-center">
                            <span class="text-gray-500">角色标识:</span>
                            <span v-for="role in permissions.roles" :key="role" class="px-2 py-0.5 bg-gray-200 text-gray-700 rounded text-[10px] font-mono font-bold">
                                {{ role }}
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Section Grid (Mobile Friendly) -->
                <div v-if="userInfo.role !== 'admin'" class="grid grid-cols-1 gap-4">
                    <!-- Agents -->
                    <div class="border border-gray-200 rounded-lg overflow-hidden bg-white shadow-sm">
                        <div class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                            <h3 class="text-xs sm:text-sm font-bold text-gray-900 flex items-center gap-2">
                                <span class="p-1 bg-blue-100 text-blue-600 rounded">
                                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
                                </span>
                                智能体 (Agents)
                            </h3>
                            <span class="text-[10px] font-bold text-gray-400 bg-white px-2 py-0.5 rounded-full border">
                                {{ permissions.details?.agents?.length || 0 }}
                            </span>
                        </div>
                        <div class="p-3">
                            <div v-if="!permissions.details?.agents?.length" class="text-center text-gray-400 py-4 text-xs italic">（暂无授权）</div>
                            <div v-else class="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-3 gap-2">
                                <div v-for="item in permissions.details.agents" :key="item.id" class="flex flex-col p-2.5 rounded-lg border border-gray-100 bg-white hover:border-blue-200 transition-all shadow-sm">
                                    <span class="font-bold text-gray-800 truncate text-xs">{{ item.display_name || item.name }}</span>
                                    <span class="text-[9px] text-gray-400 font-mono truncate mt-0.5">{{ item.name }}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Knowledge bases (RAGFlow datasets) -->
                    <div class="border border-gray-200 rounded-lg overflow-hidden bg-white shadow-sm">
                        <div class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                            <h3 class="text-xs sm:text-sm font-bold text-gray-900 flex items-center gap-2">
                                <span class="p-1 bg-green-100 text-green-600 rounded">
                                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>
                                </span>
                                知识库
                            </h3>
                            <span class="text-[10px] font-bold text-gray-400 bg-white px-2 py-0.5 rounded-full border">
                                {{ permissions.details?.datasets?.length || 0 }}
                            </span>
                        </div>
                        <div class="p-3">
                            <div v-if="!permissions.details?.datasets?.length" class="text-center text-gray-400 py-4 text-xs italic">（暂无授权）</div>
                            <div v-else class="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-3 gap-2">
                                <div v-for="item in permissions.details.datasets" :key="item.id" class="flex flex-col p-2.5 rounded-lg border border-gray-100 bg-white hover:border-green-200 transition-all shadow-sm">
                                    <span class="font-bold text-gray-800 truncate text-xs">{{ item.display_name || item.name }}</span>
                                    <span class="text-[9px] text-gray-400 font-mono truncate mt-0.5">{{ item.id }}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- ChatBI metadata datasets -->
                    <div class="border border-gray-200 rounded-lg overflow-hidden bg-white shadow-sm">
                        <div class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                            <h3 class="text-xs sm:text-sm font-bold text-gray-900 flex items-center gap-2">
                                <span class="p-1 bg-orange-100 text-orange-600 rounded">
                                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" /></svg>
                                </span>
                                数据集
                            </h3>
                            <span class="text-[10px] font-bold text-gray-400 bg-white px-2 py-0.5 rounded-full border">
                                {{ permissions.details?.metadata?.length || 0 }}
                            </span>
                        </div>
                        <div class="p-3">
                            <div v-if="!permissions.details?.metadata?.length" class="text-center text-gray-400 py-4 text-xs italic">（暂无授权）</div>
                            <div v-else class="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-3 gap-2">
                                <div v-for="item in permissions.details.metadata" :key="item.id" class="flex flex-col p-2.5 rounded-lg border border-gray-100 bg-white hover:border-orange-200 transition-all shadow-sm">
                                    <span class="font-bold text-gray-800 truncate text-xs">{{ item.display_name || item.name }}</span>
                                    <span class="text-[9px] text-gray-400 font-mono truncate mt-0.5">ID: {{ item.id }}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Other resources (Menus, Elements) -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                         <!-- Menus -->
                        <div class="border border-gray-200 rounded-lg overflow-hidden bg-white shadow-sm">
                            <div class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                                <h3 class="text-xs font-bold text-gray-900 flex items-center gap-2">界面菜单</h3>
                                <span class="text-[10px] font-bold text-gray-400">{{ permissions.details?.menus?.length || 0 }}</span>
                            </div>
                            <div class="p-3 flex flex-wrap gap-1.5">
                                <span v-for="item in permissions.details?.menus" :key="item.id" class="px-2 py-1 bg-indigo-50 text-indigo-700 rounded text-[10px] font-bold border border-indigo-100">{{ item.display_name }}</span>
                            </div>
                        </div>
                         <!-- Elements -->
                        <div class="border border-gray-200 rounded-lg overflow-hidden bg-white shadow-sm">
                            <div class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                                <h3 class="text-xs font-bold text-gray-900 flex items-center gap-2">功能点</h3>
                                <span class="text-[10px] font-bold text-gray-400">{{ permissions.details?.elements?.length || 0 }}</span>
                            </div>
                            <div class="p-3 flex flex-wrap gap-1.5">
                                <span v-for="item in permissions.details?.elements" :key="item.id" class="px-2 py-1 bg-rose-50 text-rose-700 rounded text-[10px] font-bold border border-rose-100">{{ item.display_name }}</span>
                            </div>
                        </div>
                    </div>
                </div>
                </template>
            </div>
        </div>

        <!-- Memory Tab -->
        <div v-else-if="activeTab === 'memory'" class="space-y-5" @click="closeMemoryMenu">
            <!-- 单层分段导航 + 危险操作靠右弱化 -->
            <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div class="inline-flex w-fit max-w-full overflow-x-auto rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
                    <button
                        type="button"
                        class="whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors sm:text-sm"
                        :class="memoryView === 'daily' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
                        @click="switchMemoryView('daily')"
                    >
                        每日摘要
                    </button>
                    <button
                        type="button"
                        class="whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors sm:text-sm"
                        :class="memoryView === 'session' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
                        @click="switchMemoryView('session')"
                    >
                        会话摘要
                    </button>
                    <button
                        type="button"
                        class="whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors sm:text-sm"
                        :class="memoryView === 'ltm' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
                        @click="switchMemoryView('ltm')"
                    >
                        长期记忆
                    </button>
                </div>

                <div class="flex items-center gap-2 self-end sm:self-auto">
                    <button
                        v-if="memoryView === 'ltm'"
                        type="button"
                        class="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95"
                        @click="openLtmModal()"
                    >
                        <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" /></svg>
                        新增偏好
                    </button>
                    <button
                        v-if="memoryView === 'daily' || memoryView === 'session'"
                        type="button"
                        class="rounded-lg px-2.5 py-1.5 text-xs text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                        :disabled="clearingAllSessionMemory"
                        title="清除全部会话摘要、每日摘要与聊天明细"
                        @click.stop="confirmClearAllSessionMemory"
                    >
                        {{ clearingAllSessionMemory ? '清除中…' : '清除全部记忆' }}
                    </button>
                </div>
            </div>

            <!-- 每日摘要 -->
            <div v-if="memoryView === 'daily'" class="space-y-4">
                <div class="flex flex-col gap-3">
                    <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <div class="relative min-w-0 flex-1">
                            <input
                                v-model="dailyKeyword"
                                class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                                placeholder="搜索每日摘要主题..."
                                @keyup.enter="fetchMyDailySummaries"
                            />
                        </div>
                        <div class="flex shrink-0 items-center gap-2">
                            <button
                                type="button"
                                class="rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50"
                                :class="showMemoryDateFilter ? 'border-blue-300 text-blue-600' : ''"
                                @click="showMemoryDateFilter = !showMemoryDateFilter"
                            >
                                日期筛选
                            </button>
                            <button
                                type="button"
                                class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95"
                                @click="fetchMyDailySummaries"
                            >
                                查询
                            </button>
                        </div>
                    </div>
                    <div v-if="showMemoryDateFilter" class="flex flex-wrap items-end gap-3 rounded-lg border border-gray-100 bg-gray-50/80 p-3">
                        <div>
                            <label class="mb-1 block text-xs text-gray-500">开始日期</label>
                            <input v-model="dailyDateFrom" type="date" class="rounded-lg border border-gray-300 bg-white px-2 py-2 text-sm shadow-sm outline-none" />
                        </div>
                        <div>
                            <label class="mb-1 block text-xs text-gray-500">结束日期</label>
                            <input v-model="dailyDateTo" type="date" class="rounded-lg border border-gray-300 bg-white px-2 py-2 text-sm shadow-sm outline-none" />
                        </div>
                    </div>
                </div>

                <div v-if="dailyLoading" class="flex justify-center py-10">
                    <div class="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600"></div>
                </div>

                <div v-else-if="myDailySummaries.length === 0" class="rounded-xl border border-dashed border-gray-200 bg-gray-50/50 px-6 py-12 text-center">
                    <p class="text-sm font-medium text-gray-600">还没有每日摘要</p>
                    <p class="mt-1 text-xs text-gray-400">和助手聊过天后，系统会按日汇总；也可在有会话后手动重建某一天。</p>
                </div>

                <div v-else class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                    <div
                        v-for="row in myDailySummaries"
                        :key="row.date"
                        class="group cursor-pointer border-b border-gray-100 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-blue-50/30"
                        @click="openDailyDetail(row)"
                    >
                        <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0 flex-1 space-y-1.5">
                                <div class="flex flex-wrap items-center gap-2">
                                    <span class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 font-mono text-xs font-semibold text-gray-700">{{ row.date }}</span>
                                    <span class="truncate text-sm font-semibold text-gray-900">{{ row.title || '今日记忆' }}</span>
                                    <span class="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-600">{{ row.session_count || 0 }} 个会话</span>
                                </div>
                                <p class="line-clamp-2 text-xs leading-relaxed text-gray-500">{{ row.summary || '暂无摘要' }}</p>
                                <p class="text-[10px] text-gray-400">更新于 {{ formatTime(row.last_active) }}</p>
                            </div>
                            <div class="relative flex shrink-0 items-center gap-1" @click.stop>
                                <button
                                    type="button"
                                    class="rounded-lg px-2.5 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
                                    @click="openDailyDetail(row)"
                                >
                                    详情
                                </button>
                                <button
                                    type="button"
                                    class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                                    aria-label="更多操作"
                                    @click="toggleMemoryMenu(`daily:${row.date}`, $event)"
                                >
                                    <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 20 20"><path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
                                </button>
                                <div
                                    v-if="memoryMenuKey === `daily:${row.date}`"
                                    class="absolute right-0 top-8 z-20 w-28 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
                                >
                                    <button type="button" class="block w-full px-3 py-1.5 text-left text-xs text-amber-700 hover:bg-amber-50" @click="confirmRebuildDaily(row); closeMemoryMenu()">重建</button>
                                    <button type="button" class="block w-full px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50" @click="confirmDeleteDaily(row); closeMemoryMenu()">删除</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 会话摘要 -->
            <div v-else-if="memoryView === 'session'" class="space-y-4">
                <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <input
                        v-model="sessionsKeyword"
                        class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                        placeholder="搜索会话标题或摘要..."
                        @keyup.enter="fetchMySessions"
                    />
                    <button
                        type="button"
                        class="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95"
                        @click="fetchMySessions"
                    >
                        查询
                    </button>
                </div>

                <div v-if="sessionsLoading" class="flex justify-center py-10">
                    <div class="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600"></div>
                </div>

                <div v-else-if="mySessions.length === 0" class="rounded-xl border border-dashed border-gray-200 bg-gray-50/50 px-6 py-12 text-center">
                    <p class="text-sm font-medium text-gray-600">还没有会话摘要</p>
                    <p class="mt-1 text-xs text-gray-400">和助手完成一轮对话后，系统会自动生成可回顾的会话摘要。</p>
                </div>

                <div v-else class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                    <div
                        v-for="session in mySessions"
                        :key="session.conversation_id"
                        class="group cursor-pointer border-b border-gray-100 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-blue-50/30"
                        @click="openSessionDetail(session)"
                    >
                        <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0 flex-1 space-y-1.5">
                                <div class="flex flex-wrap items-center gap-2">
                                    <span class="truncate text-sm font-semibold text-gray-900" :title="session.title">{{ session.title || '无标题会话' }}</span>
                                    <span class="rounded-full bg-gray-100 px-2 py-0.5 font-mono text-[10px] text-gray-500" :title="session.conversation_id">
                                        {{ session.conversation_id.substring(0, 8) }}…
                                    </span>
                                    <span
                                        class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                                        :class="session.has_history ? 'bg-blue-50 text-blue-600' : 'bg-gray-50 text-gray-400'"
                                    >
                                        {{ session.has_history ? '含明细' : '无明细' }}
                                    </span>
                                </div>
                                <p class="line-clamp-2 text-xs leading-relaxed text-gray-500">{{ session.summary || '暂无摘要' }}</p>
                                <p class="text-[10px] text-gray-400">活跃于 {{ formatTime(session.last_active) }}</p>
                            </div>
                            <div class="relative flex shrink-0 items-center gap-1" @click.stop>
                                <button
                                    type="button"
                                    class="rounded-lg px-2.5 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
                                    @click="openSessionDetail(session)"
                                >
                                    详情
                                </button>
                                <button
                                    type="button"
                                    class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                                    aria-label="更多操作"
                                    @click="toggleMemoryMenu(`session:${session.conversation_id}`, $event)"
                                >
                                    <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 20 20"><path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
                                </button>
                                <div
                                    v-if="memoryMenuKey === `session:${session.conversation_id}`"
                                    class="absolute right-0 top-8 z-20 w-28 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
                                >
                                    <button type="button" class="block w-full px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50" @click="confirmDeleteSession(session); closeMemoryMenu()">清除记忆</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 长期记忆 -->
            <div v-else-if="memoryView === 'ltm'" class="space-y-4">
                <p class="text-xs leading-relaxed text-gray-500">
                    长期记忆是助手跨会话记住的个人偏好与事实（如常用语言、角色习惯等）。
                </p>

                <div v-if="ltmLoading" class="flex justify-center py-10">
                    <div class="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600"></div>
                </div>

                <div v-else-if="Object.keys(myLtm).length === 0" class="rounded-xl border border-dashed border-gray-200 bg-gray-50/50 px-6 py-12 text-center">
                    <p class="text-sm font-medium text-gray-600">还没有长期偏好</p>
                    <p class="mt-1 text-xs text-gray-400">对话中助手可自动沉淀，也可手动「新增偏好」。</p>
                </div>

                <div v-else class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs sm:text-sm">
                            <thead class="border-b border-gray-200 bg-gray-50 text-gray-500">
                                <tr>
                                    <th class="px-4 py-3 font-semibold">偏好键</th>
                                    <th class="px-4 py-3 font-semibold">内容</th>
                                    <th class="w-28 px-4 py-3 text-right font-semibold">操作</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-gray-100">
                                <tr v-for="(val, key) in myLtm" :key="key" class="hover:bg-gray-50/60">
                                    <td class="px-4 py-3 font-mono text-xs font-semibold text-gray-800">{{ key }}</td>
                                    <td class="px-4 py-3 whitespace-pre-wrap break-all leading-relaxed text-gray-600">{{ val }}</td>
                                    <td class="space-x-2 whitespace-nowrap px-4 py-3 text-right">
                                        <button type="button" class="text-xs font-medium text-blue-600 hover:underline" @click="openLtmModal(String(key), String(val))">编辑</button>
                                        <button type="button" class="text-xs font-medium text-red-600 hover:underline" @click="confirmDeleteLtm(String(key))">删除</button>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Token Usage Tab -->
        <div v-else-if="activeTab === 'tokens'">
            <PersonalTokenUsage />
        </div>

        <!-- Notifications Tab -->
        <div v-else-if="activeTab === 'notifications'">
            <NotificationConfigs @show-toast="showToast" />
        </div>

        <!-- My Data Tab -->
        <div v-else-if="activeTab === 'data'">
            <DataPortalHome embedded />
        </div>

        <div v-else-if="activeTab === 'skills'" class="px-4 pb-4 sm:px-6 sm:pb-6">
            <SkillsManagement
                personal-only
                :initial-skill-id="skillsInitialId"
                @drawer-closed="clearSkillsDeepLink"
            />
        </div>

        </div>

    </div>

    <!-- 会话记忆详情 Modal -->
    <Modal
        v-if="showSessionDetailModal"
        :show="showSessionDetailModal"
        :title="sessionDetail?.title || '会话记忆详情'"
        size="max-w-4xl"
        @close="showSessionDetailModal = false"
    >
        <div v-if="sessionDetailLoading" class="flex justify-center py-10">
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
        <div v-else class="space-y-4">
            <div class="grid grid-cols-2 gap-3 text-xs bg-gray-50 p-3 rounded-lg border">
                <div>
                    <span class="text-gray-400 block font-bold">会话 ID</span>
                    <span class="font-mono text-gray-700 break-all">{{ sessionDetail?.conversation_id }}</span>
                </div>
                <div>
                    <span class="text-gray-400 block font-bold">最后活跃时间</span>
                    <span class="text-gray-700">{{ formatTime(sessionDetail?.last_active) }}</span>
                </div>
            </div>
            
            <div v-if="sessionDetail?.summary" class="space-y-1">
                <h4 class="text-sm font-semibold text-gray-800 border-b pb-1">会话摘要</h4>
                <p class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap font-sans bg-blue-50/20 p-3 rounded-lg border border-blue-100/50">{{ sessionDetail.summary }}</p>
            </div>

            <div 
                v-for="section in [
                    { label: '关键事实', value: sessionDetail?.key_facts },
                    { label: '已确认决策', value: sessionDetail?.decisions },
                    { label: '未完成事项', value: sessionDetail?.open_items },
                    { label: '实体/关键词', value: sessionDetail?.entities }
                ]"
                :key="section.label"
                v-show="formatList(section.value).length > 0"
                class="space-y-1.5"
            >
                <h4 class="text-sm font-semibold text-gray-800">{{ section.label }}</h4>
                <div class="flex flex-wrap gap-1.5">
                    <span 
                        v-for="item in formatList(section.value)" 
                        :key="item"
                        class="inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700 border border-gray-200"
                    >
                        {{ item }}
                    </span>
                </div>
            </div>

            <div class="space-y-2">
                <h4 class="text-sm font-semibold text-gray-800 border-b pb-1 flex items-center justify-between">
                    <span>聊天消息明细</span>
                    <span class="text-xs text-gray-400 font-normal">（共 {{ sessionHistory.length }} 条记录）</span>
                </h4>
                <div v-if="sessionHistory.length === 0" class="text-sm text-gray-400 py-4 text-center">无 Redis 历史聊天明细</div>
                <div v-else class="space-y-3 border border-gray-100 rounded-lg p-3 bg-gray-50/50 max-h-72 overflow-y-auto">
                    <div v-for="(m, i) in sessionHistory" :key="i" class="text-sm flex items-start gap-2">
                        <span 
                            class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold mt-0.5 flex-shrink-0"
                            :class="m.role === 'user' ? 'bg-blue-100 text-blue-800' : 'bg-gray-200 text-gray-700'"
                        >
                            {{ m.role === 'user' ? '用户' : '助手' }}
                        </span>
                        <span class="text-gray-700 whitespace-pre-wrap break-all">{{ m.content }}</span>
                    </div>
                </div>
            </div>
        </div>
    </Modal>

    <!-- LTM 新增与编辑 Modal -->
    <Modal
        v-if="showLtmModal"
        :show="showLtmModal"
        :title="ltmForm.isEdit ? '编辑长期偏好' : '新增长期偏好'"
        size="max-w-md"
        @close="showLtmModal = false"
    >
        <form @submit.prevent="saveMyLtm" class="space-y-4">
            <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">偏好键 (Key)</label>
                <input 
                    v-model="ltmForm.key"
                    type="text" 
                    class="block w-full px-3 py-2.5 bg-gray-50 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm font-mono transition-all"
                    placeholder="例如: language, user_role 等"
                    required
                />
            </div>
            <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">偏好内容 (Value)</label>
                <textarea 
                    v-model="ltmForm.value"
                    rows="3"
                    class="block w-full px-3 py-2.5 bg-gray-50 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all"
                    placeholder="输入具体的个性习惯或核心偏好值..."
                    required
                ></textarea>
            </div>
            <div class="flex justify-end gap-3 pt-2">
                <button 
                    type="button"
                    @click="showLtmModal = false"
                    class="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-50 transition-all"
                >
                    取消
                </button>
                <button 
                    type="submit"
                    class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-bold hover:bg-blue-700 active:scale-95 transition-all shadow-sm"
                >
                    确认保存
                </button>
            </div>
        </form>
    </Modal>

    <!-- 会话删除确认 Modal -->
    <ConfirmModal
        v-if="showDeleteSessionConfirm"
        title="清除会话记忆"
        :message="`确定清除该会话的记忆摘要及 Redis 中的历史聊天纪录？清除后，该会话的历史事实将不会自动注入未来的会话上下文。此操作无法撤销。`"
        confirm-text="确认清除"
        type="danger"
        @confirm="executeDeleteSession"
        @cancel="showDeleteSessionConfirm = false"
    />

    <!-- 一键清除全部会话记忆确认 Modal -->
    <ConfirmModal
        v-if="showClearAllSessionMemoryConfirm"
        title="一键清除所有记忆"
        message="确定清除全部会话记忆？将删除所有会话摘要、每日摘要及 Redis 中的历史聊天记录。清除后，这些历史事实将不再自动注入未来的会话上下文。此操作无法撤销。"
        confirm-text="确认清除"
        type="danger"
        @confirm="executeClearAllSessionMemory"
        @cancel="showClearAllSessionMemoryConfirm = false"
    />

    <!-- LTM 删除确认 Modal -->
    <ConfirmModal
        v-if="showDeleteLtmConfirm"
        title="删除偏好记忆"
        :message="`确定删除长期记忆中的 '${ltmKeyToDelete}' 项？删除后，智能体在新的会话中将遗忘这一项用户核心事实与行为习惯偏好。`"
        confirm-text="确认删除"
        type="danger"
        @confirm="executeDeleteLtm"
        @cancel="showDeleteLtmConfirm = false"
    />

    <!-- 每日摘要详情 Modal -->
    <Modal
        v-if="showDailyDetailModal"
        :show="showDailyDetailModal"
        :title="`每日记忆详情 · ${dailyDetail?.date || ''}`"
        size="max-w-4xl"
        @close="showDailyDetailModal = false"
    >
        <div v-if="dailyDetailLoading" class="flex justify-center py-10">
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
        <div v-else class="space-y-4">
            <div class="grid grid-cols-2 gap-3 text-xs bg-gray-50 p-3 rounded-lg border">
                <div>
                    <span class="text-gray-400 block font-bold">生成日期</span>
                    <span class="font-mono text-gray-700 font-bold">{{ dailyDetail?.date }}</span>
                </div>
                <div>
                    <span class="text-gray-400 block font-bold">最后更新时间</span>
                    <span class="text-gray-700">{{ formatTime(dailyDetail?.last_active) }}</span>
                </div>
            </div>
            
            <div v-if="dailyDetail?.summary" class="space-y-1">
                <h4 class="text-sm font-semibold text-gray-800 border-b pb-1">今日记忆摘要</h4>
                <p class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap font-sans bg-blue-50/20 p-3 rounded-lg border border-blue-100/50">{{ dailyDetail.summary }}</p>
            </div>

            <div 
                v-for="section in [
                    { label: '今日主要讨论主题 (Topics)', value: dailyDetail?.topics },
                    { label: '今日已确认决策', value: dailyDetail?.decisions },
                    { label: '今日未完成事项', value: dailyDetail?.open_items },
                    { label: '关联实体/关键词', value: dailyDetail?.entities }
                ]"
                :key="section.label"
                v-show="formatList(section.value).length > 0"
                class="space-y-1.5"
            >
                <h4 class="text-sm font-semibold text-gray-800">{{ section.label }}</h4>
                <div class="flex flex-wrap gap-1.5">
                    <span 
                        v-for="item in formatList(section.value)" 
                        :key="item"
                        class="inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700 border border-gray-200"
                    >
                        {{ item }}
                    </span>
                </div>
            </div>

            <div class="space-y-2">
                <h4 class="text-sm font-semibold text-gray-800 border-b pb-1 flex items-center justify-between">
                    <span>关联会话摘要</span>
                    <span class="text-xs text-gray-400 font-normal">（共 {{ dailySessions.length }} 个会话）</span>
                </h4>
                <div v-if="dailySessions.length === 0" class="text-sm text-gray-400 py-4 text-center">当天暂无关联的会话摘要</div>
                <div v-else class="border border-gray-100 rounded-lg overflow-hidden">
                    <button
                        v-for="session in dailySessions"
                        :key="session.conversation_id"
                        type="button"
                        class="w-full text-left px-4 py-3 border-b border-gray-100 last:border-b-0 hover:bg-gray-50 transition-colors"
                        @click="openSessionDetail(session)"
                    >
                        <div class="flex items-center justify-between gap-3">
                            <span class="font-bold text-sm text-gray-900 truncate">{{ session.title || '无标题会话' }}</span>
                            <span
                                class="shrink-0 text-[10px] px-2 py-0.5 rounded-full font-bold"
                                :class="session.has_history ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-500'"
                            >
                                {{ session.has_history ? '有 Redis 明细' : '无明细' }}
                            </span>
                        </div>
                        <p v-if="session.summary" class="text-xs text-gray-500 truncate mt-1">{{ session.summary }}</p>
                        <p class="text-[10px] font-mono text-gray-400 mt-1">ID: {{ session.conversation_id }}</p>
                    </button>
                </div>
            </div>
        </div>
    </Modal>

    <!-- 每日摘要重建确认 Modal -->
    <ConfirmModal
        v-if="showRebuildDailyConfirm"
        title="重建每日摘要"
        :message="`将根据当天所有会话重新聚合生成 '${dailyToRebuild?.date}' 的每日摘要。确认执行？`"
        confirm-text="确定重建"
        type="warning"
        @confirm="executeRebuildDaily"
        @cancel="showRebuildDailyConfirm = false"
    />

    <!-- 每日摘要删除确认 Modal -->
    <ConfirmModal
        v-if="showDeleteDailyConfirm"
        title="删除每日摘要"
        :message="`确定删除 '${dailyToDelete?.date}' 的每日摘要？该操作仅删除这一天的聚合摘要归档，关联会话的独立摘要与 Redis 明细不会被删除。`"
        confirm-text="确认删除"
        type="danger"
        @confirm="executeDeleteDaily"
        @cancel="showDeleteDailyConfirm = false"
    />
    
    <Toast 
      v-if="toast.show" 
      :key="toast.key"
      :message="toast.message" 
      :type="toast.type" 
      @close="closeToast" 
    />
</div>
</template>
