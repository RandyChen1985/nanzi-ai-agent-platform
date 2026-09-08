<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from '../utils/axios'
import Toast from '../components/Toast.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import { useBranding } from '../composables/useBranding'
import { renderMarkdown } from '../utils/markdown'
import { copyToClipboard } from '../utils/clipboard'
import { generateQRCodeDataUrl } from '../utils/qrcode'
import { checkPasswordPolicy } from '../utils/passwordPolicy'

const { branding, loadBranding } = useBranding()

const userInfo = ref<any>({})
const userApiKey = ref('')
const loadingApiKey = ref(false)
const apiKeyRevealed = ref(false)
const newPassword = ref('')
const confirmPassword = ref('')
const loadingPassword = ref(false)
const infoSubTab = ref<'profile' | 'security' | 'browser'>('profile')

const passwordPolicyResult = computed(() => checkPasswordPolicy(newPassword.value, userInfo.value?.user_name))

// 密码修改周期与提醒计算属性
const passwordExpireWarningClass = computed(() => {
    const info = userInfo.value?.password_info
    if (!info?.has_password) return 'bg-amber-50/60 border-amber-200/80 text-amber-900'
    if (info.is_expired) return 'bg-rose-50/70 border-rose-200 text-rose-900'
    if (info.days_until_next_change <= 7) return 'bg-amber-50/70 border-amber-200 text-amber-900'
    return 'bg-blue-50/50 border-blue-100 text-blue-900'
})

const passwordExpireIconClass = computed(() => {
    const info = userInfo.value?.password_info
    if (!info?.has_password) return 'bg-amber-100 text-amber-700'
    if (info.is_expired) return 'bg-rose-100 text-rose-700'
    if (info.days_until_next_change <= 7) return 'bg-amber-100 text-amber-700'
    return 'bg-blue-100 text-blue-700'
})

const passwordExpireBadgeClass = computed(() => {
    const info = userInfo.value?.password_info
    if (!info?.has_password) return 'bg-amber-100 text-amber-800'
    if (info.is_expired) return 'bg-rose-100 text-rose-800 font-bold'
    if (info.days_until_next_change <= 7) return 'bg-amber-100 text-amber-800 font-bold'
    return 'bg-blue-100 text-blue-800'
})

const passwordExpireTextClass = computed(() => {
    const info = userInfo.value?.password_info
    if (!info?.has_password) return 'text-amber-700'
    if (info.is_expired) return 'text-rose-700 font-medium'
    if (info.days_until_next_change <= 7) return 'text-amber-700 font-medium'
    return 'text-blue-700/90'
})

const passwordExpireButtonClass = computed(() => {
    const info = userInfo.value?.password_info
    if (!info?.has_password) return 'bg-amber-600 hover:bg-amber-700 text-white border-transparent'
    if (info.is_expired) return 'bg-rose-600 hover:bg-rose-700 text-white border-transparent'
    if (info.days_until_next_change <= 7) return 'bg-amber-600 hover:bg-amber-700 text-white border-transparent'
    return 'bg-white hover:bg-blue-50 text-blue-700 border-blue-200'
})

// 2FA Two-Factor Authentication Logic
const twoFactorEnabled = computed(() => !!userInfo.value?.two_factor_enabled)
const showSetupModal = ref(false)
const loadingSetup = ref(false)
const setupSecret = ref('')
const setupOtpUrl = ref('')
const setupQrDataUrl = ref('')
const setupCode = ref('')
const loadingEnable2FA = ref(false)

const showDisableModal = ref(false)
const disableType = ref<'code' | 'password'>('code')
const disableCode = ref('')
const disablePassword = ref('')
const loadingDisable2FA = ref(false)

const openSetupModal = async () => {
    loadingSetup.value = true
    showSetupModal.value = true
    setupCode.value = ''
    try {
        const res = await axios.post('/api/portal/auth/2fa/setup')
        if (res.data?.status === 'success') {
            setupSecret.value = res.data.data.secret
            setupOtpUrl.value = res.data.data.otpauth_url
            setupQrDataUrl.value = await generateQRCodeDataUrl(res.data.data.otpauth_url)
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '发起两步验证绑定失败，请稍后重试', 'error')
        showSetupModal.value = false
    } finally {
        loadingSetup.value = false
    }
}

const handleEnable2FA = async () => {
    if (!setupCode.value || setupCode.value.trim().length !== 6) {
        showToast('请输入 6 位动态验证码', 'warning')
        return
    }
    loadingEnable2FA.value = true
    try {
        const res = await axios.post('/api/portal/auth/2fa/enable', {
            code: setupCode.value.trim()
        })
        if (res.data?.status === 'success') {
            showToast('Google 两步验证已成功开启！', 'success')
            showSetupModal.value = false
            await fetchUserInfo()
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '动态验证码错误，开启失败', 'error')
    } finally {
        loadingEnable2FA.value = false
    }
}

const openDisableModal = () => {
    disableCode.value = ''
    disablePassword.value = ''
    disableType.value = 'code'
    showDisableModal.value = true
}

const handleDisable2FA = async () => {
    const payload: any = {}
    if (disableType.value === 'code') {
        if (!disableCode.value || disableCode.value.trim().length !== 6) {
            showToast('请输入 6 位动态验证码', 'warning')
            return
        }
        payload.code = disableCode.value.trim()
    } else {
        if (!disablePassword.value) {
            showToast('请输入当前登录密码', 'warning')
            return
        }
        payload.password = disablePassword.value
    }
    loadingDisable2FA.value = true
    try {
        const res = await axios.post('/api/portal/auth/2fa/disable', payload)
        if (res.data?.status === 'success') {
            showToast('两步验证已成功关闭', 'success')
            showDisableModal.value = false
            await fetchUserInfo()
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '关闭失败，请检查输入的验证码或密码', 'error')
    } finally {
        loadingDisable2FA.value = false
    }
}

const copySecret = async () => {
    if (!setupSecret.value) return
    const success = await copyToClipboard(setupSecret.value)
    if (success) {
        showToast('密钥已复制到剪贴板', 'success')
    } else {
        showToast('复制失败，请手动复制', 'error')
    }
}

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

// 重置 API Key 状态与方法
const showResetKeyModal = ref(false)
const resetVerifyType = ref<'password' | 'code'>('password')
const resetPassword = ref('')
const resetCode = ref('')
const loadingResetKey = ref(false)

const openResetApiKeyModal = () => {
    resetVerifyType.value = 'password'
    resetPassword.value = ''
    resetCode.value = ''
    showResetKeyModal.value = true
}

const closeResetApiKeyModal = () => {
    showResetKeyModal.value = false
    resetPassword.value = ''
    resetCode.value = ''
    loadingResetKey.value = false
}

const handleResetApiKey = async () => {
    if (twoFactorEnabled.value) {
        if (resetVerifyType.value === 'code') {
            if (!resetCode.value || resetCode.value.trim().length !== 6) {
                showToast('请输入 6 位 Google 动态验证码', 'warning')
                return
            }
        } else {
            if (!resetPassword.value) {
                showToast('请输入当前登录密码', 'warning')
                return
            }
        }
    } else {
        if (!resetPassword.value) {
            showToast('请输入当前登录密码', 'warning')
            return
        }
    }

    loadingResetKey.value = true
    try {
        const payload: any = {}
        if (twoFactorEnabled.value && resetVerifyType.value === 'code') {
            payload.code = resetCode.value.trim()
        } else {
            payload.password = resetPassword.value
        }

        const res = await axios.post('/api/portal/auth/api-key/reset', payload)
        if (res.data && res.data.status === 'success') {
            userApiKey.value = res.data.api_key
            apiKeyRevealed.value = true
            showToast('API Key 重置成功', 'success')
            closeResetApiKeyModal()
        } else {
            showToast(res.data?.message || '重置失败', 'error')
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '重置失败，请检查密码或验证码', 'error')
    } finally {
        loadingResetKey.value = false
    }
}

const handlePasswordChange = async () => {
    const policy = checkPasswordPolicy(newPassword.value, userInfo.value?.user_name)
    if (!policy.valid) {
        showToast(policy.message, 'warning')
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
            await fetchUserInfo()
            // 派发全局用户信息更新事件，通知全局 Top Banner 与其他视图即时刷新
            window.dispatchEvent(new CustomEvent('user-info-updated'))
        } else {
            showToast('修改失败', 'error')
        }
    } catch (e: any) {
        showToast(e.response?.data?.detail || '修改失败', 'error')
    } finally {
        loadingPassword.value = false
    }
}

const clearingBrowserData = ref(false)
const showClearBrowserModal = ref(false)
const browserCacheSizeBytes = ref<number | null>(null)
const loadingCacheSize = ref(false)

const browserCacheSizeDisplay = computed(() => {
    const bytes = browserCacheSizeBytes.value
    if (bytes === null) return null
    if (bytes === 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    let val = bytes
    let idx = 0
    while (val >= 1024 && idx < units.length - 1) {
        val /= 1024
        idx++
    }
    return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[idx]}`
})

const fetchBrowserCacheSize = async () => {
    loadingCacheSize.value = true
    try {
        const res = await axios.get('/api/v1/chat/browser/profiles')
        const profiles: Array<{ disk_size_bytes?: number | null }> = res.data || []
        const total = profiles.reduce((acc, p) => acc + (p.disk_size_bytes ?? 0), 0)
        browserCacheSizeBytes.value = total
    } catch {
        browserCacheSizeBytes.value = null
    } finally {
        loadingCacheSize.value = false
    }
}

const handleClearBrowserData = () => {
    showClearBrowserModal.value = true
}

const confirmClearBrowserData = async () => {
    clearingBrowserData.value = true
    try {
        await axios.delete('/api/v1/chat/browser/profiles/clear')
        showClearBrowserModal.value = false
        browserCacheSizeBytes.value = 0
        showToast('云端自动化浏览器历史、登录态及文件缓存已彻底清除', 'success')
    } catch (e: any) {
        showToast(e.response?.data?.detail || '清除失败，请稍后重试', 'error')
    } finally {
        clearingBrowserData.value = false
    }
}

import { watch } from 'vue'
import PersonalTokenUsage from '../components/personal/PersonalTokenUsage.vue'
import PersonalMemoryPanel from '../components/personal/PersonalMemoryPanel.vue'
import NotificationConfigs from '../components/personal/NotificationConfigs.vue'
import DataPortalHome from './DataPortalHome.vue'
import SkillsManagement from './SkillsManagement.vue'
import McpManagement from './McpManagement.vue'
import TaskCenter from './TaskCenter.vue'
import { useRoute, useRouter } from 'vue-router'

type PersonalTab = 'info' | 'permissions' | 'memory' | 'tokens' | 'notifications' | 'data' | 'skills' | 'mcp' | 'tasks'
const route = useRoute()
const router = useRouter()
const personalTabs: PersonalTab[] = ['info', 'permissions', 'memory', 'tokens', 'notifications', 'data', 'skills', 'mcp', 'tasks']
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
}, { immediate: true })

watch(() => route.query.subtab, (value) => {
    if (value === 'security' || value === 'profile' || value === 'browser') {
        infoSubTab.value = value
    }
}, { immediate: true })

onMounted(() => {
    fetchUserInfo()
    loadBranding()
    fetchBrowserCacheSize()
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
                    @click="activeTab = 'mcp'"
                    :class="[
                        activeTab === 'mcp'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的 MCP
                </button>
                <button
                    @click="activeTab = 'tasks'"
                    :class="[
                        activeTab === 'tasks'
                            ? 'border-blue-500 text-blue-600'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300',
                        'whitespace-nowrap py-3 sm:py-4 px-1 border-b-2 font-medium text-xs sm:text-sm transition-colors'
                    ]"
                >
                    我的任务
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

        <div :class="(activeTab === 'data' || activeTab === 'skills' || activeTab === 'mcp' || activeTab === 'tasks' || activeTab === 'info') ? 'pt-4 sm:pt-5' : 'px-4 pt-4 pb-4 sm:px-6 sm:pt-5 sm:pb-6'">
        <!-- Info Tab (类似我的数据门户：左侧子菜单 + 右侧主内容) -->
        <div v-if="activeTab === 'info'" class="grid grid-cols-1 overflow-hidden bg-white md:grid-cols-[200px_minmax(0,1fr)] min-h-[560px]">
            <!-- 左侧 Aside 垂直导航栏 -->
            <aside class="border-b md:border-b-0 md:border-r border-gray-100 bg-gray-50/70 p-3 sm:p-4">
                <div class="mb-4 hidden md:flex items-center gap-2 px-2 text-sm font-bold text-gray-900">
                    <span class="grid h-8 w-8 place-items-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-500/20">
                        <svg class="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                        </svg>
                    </span>
                    个人基础信息
                </div>
                <nav class="flex md:flex-col gap-1 overflow-x-auto pb-1 md:pb-0 scrollbar-none">
                    <button
                        type="button"
                        @click="infoSubTab = 'profile'"
                        class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-xs sm:text-sm transition-all cursor-pointer whitespace-nowrap"
                        :class="infoSubTab === 'profile' ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20' : 'text-gray-600 hover:bg-white hover:text-gray-900'"
                    >
                        <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V8a2 2 0 00-2-2h-5m-4 0V5a2 2 0 114 0v1m-4 0a2 2 0 104 0m-5 8a2 2 0 100-4 2 2 0 000 4zm0 0c1.306 0 2.417.835 2.83 2M9 14a3.001 3.001 0 00-2.83 2M15 11h3m-3 4h2" />
                        </svg>
                        <span>账号信息</span>
                    </button>
                    <button
                        type="button"
                        @click="infoSubTab = 'security'"
                        class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-xs sm:text-sm transition-all cursor-pointer whitespace-nowrap"
                        :class="infoSubTab === 'security' ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20' : 'text-gray-600 hover:bg-white hover:text-gray-900'"
                    >
                        <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                        <span>安全设置</span>
                    </button>
                    <button
                        type="button"
                        @click="infoSubTab = 'browser'"
                        class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-xs sm:text-sm transition-all cursor-pointer whitespace-nowrap"
                        :class="infoSubTab === 'browser' ? 'bg-blue-600 font-semibold text-white shadow-sm shadow-blue-500/20' : 'text-gray-600 hover:bg-white hover:text-gray-900'"
                    >
                        <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                        </svg>
                        <span>云端浏览器缓存</span>
                    </button>
                </nav>
            </aside>

            <!-- 右侧主内容区域 -->
            <main class="min-w-0 p-4 sm:p-6 lg:p-8">
                <!-- 1. 账号信息 -->
                <div v-if="infoSubTab === 'profile'" class="max-w-2xl space-y-6">
                    <div>
                        <h2 class="text-lg font-bold text-gray-900">账号基本信息</h2>
                        <p class="mt-1 text-xs text-gray-500">查看您的平台个人身份资料与访问凭证 API Key。</p>
                    </div>

                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 sm:p-5 bg-gray-50/80 border border-gray-100 rounded-xl">
                        <!-- 左侧：头像与基本称谓 -->
                        <div class="flex items-center min-w-0">
                            <div class="h-14 w-14 sm:h-16 sm:w-16 rounded-full bg-primary flex items-center justify-center text-xl sm:text-2xl font-bold text-white uppercase shadow-sm shrink-0">
                                {{ (userInfo.real_name || userInfo.user_name || 'U').substring(0, 2) }}
                            </div>
                            <div class="ml-4 min-w-0">
                                <p class="text-md sm:text-lg font-bold text-gray-900 truncate">{{ userInfo.real_name || userInfo.user_name }}</p>
                                <div class="flex flex-wrap items-center gap-2 mt-1">
                                    <span class="text-xs text-gray-500 font-mono">@{{ userInfo.user_name }}</span>
                                    <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] sm:text-xs font-medium" 
                                        :class="userInfo.role === 'admin' ? 'bg-purple-100 text-purple-800' : 'bg-blue-100 text-blue-800'"
                                    >
                                        {{ userInfo.role === 'admin' ? '管理员' : '普通用户' }}
                                    </span>
                                </div>
                            </div>
                        </div>

                        <!-- 右侧：用户ID与创建时间（节省空间，提升布局利用率） -->
                        <div class="flex items-center gap-4 sm:gap-6 pt-2 sm:pt-0 border-t sm:border-t-0 border-gray-200/60 sm:border-l sm:border-gray-200 sm:pl-6 shrink-0">
                            <div>
                                <span class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">用户ID</span>
                                <span class="font-mono text-gray-800 font-bold text-xs sm:text-sm">{{ userInfo.user_id }}</span>
                            </div>
                            <div class="w-px h-7 bg-gray-200 hidden sm:block"></div>
                            <div>
                                <span class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">创建时间</span>
                                <span class="text-gray-700 text-xs sm:text-sm font-medium">{{ userInfo.created_at || '-' }}</span>
                            </div>
                        </div>
                    </div>

                    <!-- 备注说明 -->
                    <div class="p-3 bg-gray-50/60 border border-gray-100 rounded-xl text-xs sm:text-sm">
                        <label class="block text-gray-400 font-medium uppercase text-[10px] mb-0.5">备注说明</label>
                        <p class="text-gray-700 mt-0.5">{{ userInfo.remark || '暂无备注' }}</p>
                    </div>

                    <!-- 密码修改周期与到期提醒（用户截图红框位置） -->
                    <div class="p-4 rounded-xl border transition-all"
                        :class="passwordExpireWarningClass"
                    >
                        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                            <div class="flex items-start sm:items-center gap-3">
                                <div class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                                    :class="passwordExpireIconClass"
                                >
                                    <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                                    </svg>
                                </div>
                                <div>
                                    <div class="flex items-center gap-2">
                                        <h4 class="text-xs sm:text-sm font-bold text-gray-800">密码安全周期提醒</h4>
                                        <span class="text-[10px] px-1.5 py-0.5 rounded font-medium"
                                            :class="passwordExpireBadgeClass"
                                        >
                                            修改周期 {{ userInfo.password_info?.password_expire_days || 30 }} 天
                                        </span>
                                    </div>
                                    <p class="text-xs mt-0.5 leading-relaxed"
                                        :class="passwordExpireTextClass"
                                    >
                                        <template v-if="userInfo.password_info?.has_password">
                                            距离上次修改密码已过 <strong class="font-bold">{{ userInfo.password_info?.days_since_last_change ?? 0 }}</strong> 天，
                                            <template v-if="(userInfo.password_info?.days_until_next_change ?? 0) > 0">
                                                还有 <strong class="font-bold">{{ userInfo.password_info?.days_until_next_change }}</strong> 天需修改密码。
                                            </template>
                                            <template v-else>
                                                已超过有效周期 <strong class="font-bold">{{ Math.abs(userInfo.password_info?.days_until_next_change ?? 0) }}</strong> 天，请尽快修改密码！
                                            </template>
                                        </template>
                                        <template v-else>
                                            当前账户尚未设置登录密码，建议及时设置密码以保障账号安全。
                                        </template>
                                    </p>
                                </div>
                            </div>
                            <button
                                type="button"
                                @click="infoSubTab = 'security'"
                                class="self-start sm:self-center px-3 py-1.5 rounded-lg text-xs font-semibold shrink-0 transition-all cursor-pointer shadow-2xs border"
                                :class="passwordExpireButtonClass"
                            >
                                {{ userInfo.password_info?.has_password ? '去修改密码' : '去设置密码' }} →
                            </button>
                        </div>
                    </div>

                    <div class="pt-4 border-t border-gray-100">
                        <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1">访问凭证 (API Key)</label>
                        <p class="text-xs text-gray-500 mb-3 leading-relaxed">用于外部脚本或智能体 CLI 免密快速认证调用。请妥善保管，切勿泄露。</p>
                        <div class="flex items-center space-x-2">
                            <input
                                type="text"
                                :value="apiKeyDisplay"
                                readonly
                                class="flex-1 px-3.5 py-2.5 border border-gray-300 rounded-lg bg-gray-50 text-xs sm:text-sm font-mono truncate shadow-inner"
                                :class="apiKeyRevealed ? 'tracking-normal' : 'tracking-widest'"
                            />
                            <button
                                v-if="!apiKeyRevealed"
                                type="button"
                                @click="revealOrFetchApiKey"
                                :disabled="loadingApiKey"
                                class="px-4 py-2.5 bg-primary text-white text-xs sm:text-sm font-bold rounded-lg hover:bg-primary-dark transition-all disabled:opacity-50 flex-shrink-0 cursor-pointer shadow-xs"
                            >
                                {{ loadingApiKey ? '...' : '查看' }}
                            </button>
                            <template v-else>
                                <button
                                    type="button"
                                    @click="copyApiKey"
                                    class="px-4 py-2.5 bg-green-600 text-white text-xs sm:text-sm font-bold rounded-lg hover:bg-green-700 transition-all flex-shrink-0 cursor-pointer shadow-xs"
                                >
                                    复制
                                </button>
                                <button
                                    type="button"
                                    @click="hideApiKey"
                                    class="px-4 py-2.5 border border-gray-300 bg-white text-gray-600 text-xs sm:text-sm font-medium rounded-lg hover:bg-gray-50 transition-all flex-shrink-0 cursor-pointer"
                                >
                                    隐藏
                                </button>
                            </template>
                            <!-- 重置 API Key 按钮 -->
                            <button
                                type="button"
                                @click="openResetApiKeyModal"
                                class="px-3.5 py-2.5 border border-rose-200 bg-rose-50/70 text-rose-600 hover:bg-rose-100 hover:border-rose-300 text-xs sm:text-sm font-bold rounded-lg transition-all flex items-center gap-1.5 flex-shrink-0 cursor-pointer shadow-2xs"
                                title="重置当前 API Key"
                            >
                                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                </svg>
                                <span>重置</span>
                            </button>
                        </div>
                    </div>
                </div>

                <!-- 2. 安全设置 -->
                <div v-else-if="infoSubTab === 'security'" class="max-w-2xl space-y-6">
                    <div>
                        <h2 class="text-lg font-bold text-gray-900">账号安全设置</h2>
                        <p class="mt-1 text-xs text-gray-500">管理您的系统登录密码与 Google 身份验证器两步认证 (2FA)。</p>
                    </div>

                    <!-- 密码修改 -->
                    <div class="p-5 border border-gray-200/80 rounded-xl bg-white shadow-2xs space-y-4">
                        <div class="flex items-center justify-between">
                            <h3 class="text-sm font-bold text-gray-800 flex items-center gap-2">
                                <span>🔑</span>
                                <span>修改登录密码</span>
                            </h3>
                            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200/60">
                                <svg class="w-3 h-3 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                                </svg>
                                <span>等保规范要求</span>
                            </span>
                        </div>

                        <!-- 等保密码复杂度规则文案说明 -->
                        <div class="p-3 bg-blue-50/70 border border-blue-100 rounded-lg text-xs space-y-1.5">
                            <div class="flex items-center gap-1.5 font-semibold text-blue-900">
                                <svg class="w-4 h-4 text-blue-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                <span>密码复杂度规则说明</span>
                            </div>
                            <p class="text-blue-800/90 leading-relaxed pl-5">
                                为符合网络安全等级保护要求，密码长度须为 <strong>8-32 位</strong>，且必须至少包含以下 4 种类型中的 <strong>3 种</strong>：<strong>大写字母 (A-Z)</strong>、<strong>小写字母 (a-z)</strong>、<strong>数字 (0-9)</strong>、<strong>特殊符号</strong>（如 !@#$%^&* 等），且不能包含空格或用户名。
                            </p>
                        </div>

                        <form @submit.prevent="handlePasswordChange" class="space-y-4">
                            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1.5">新密码</label>
                                    <input 
                                        v-model="newPassword"
                                        type="password" 
                                        class="block w-full px-3 py-2 bg-gray-50 border rounded-lg shadow-2xs focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all"
                                        :class="newPassword ? (passwordPolicyResult.valid ? 'border-emerald-400 bg-emerald-50/20' : 'border-amber-300 bg-amber-50/20') : 'border-gray-300'"
                                        placeholder="8-32位，含大/小写/数字/特殊符号中至少3种"
                                    />
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1.5">确认新密码</label>
                                    <input 
                                        v-model="confirmPassword"
                                        type="password" 
                                        class="block w-full px-3 py-2 bg-gray-50 border rounded-lg shadow-2xs focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all"
                                        :class="confirmPassword ? (confirmPassword === newPassword ? 'border-emerald-400 bg-emerald-50/20' : 'border-red-300 bg-red-50/20') : 'border-gray-300'"
                                        placeholder="再次输入新密码"
                                    />
                                </div>
                            </div>

                            <!-- 实时复杂度检测指示器 -->
                            <div v-if="newPassword" class="p-3 bg-gray-50 border border-gray-200/80 rounded-lg space-y-2 text-xs">
                                <div class="flex items-center justify-between text-gray-600 gap-2">
                                    <span class="font-medium whitespace-nowrap shrink-0">密码合规检测：</span>
                                    <span class="truncate font-medium text-right" :class="passwordPolicyResult.valid ? 'text-emerald-600 font-semibold' : 'text-amber-600'">
                                        {{ passwordPolicyResult.valid ? '✓ 符合等保要求' : passwordPolicyResult.shortMessage }}
                                    </span>
                                </div>
                                <div class="grid grid-cols-2 sm:grid-cols-5 gap-2">
                                    <div class="flex items-center gap-1.5 px-2 py-1 rounded" :class="passwordPolicyResult.hasLength ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'">
                                        <span>{{ passwordPolicyResult.hasLength ? '✓' : '○' }}</span>
                                        <span>8-32 位字符</span>
                                    </div>
                                    <div class="flex items-center gap-1.5 px-2 py-1 rounded" :class="passwordPolicyResult.hasUpper ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'">
                                        <span>{{ passwordPolicyResult.hasUpper ? '✓' : '○' }}</span>
                                        <span>大写字母</span>
                                    </div>
                                    <div class="flex items-center gap-1.5 px-2 py-1 rounded" :class="passwordPolicyResult.hasLower ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'">
                                        <span>{{ passwordPolicyResult.hasLower ? '✓' : '○' }}</span>
                                        <span>小写字母</span>
                                    </div>
                                    <div class="flex items-center gap-1.5 px-2 py-1 rounded" :class="passwordPolicyResult.hasDigit ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'">
                                        <span>{{ passwordPolicyResult.hasDigit ? '✓' : '○' }}</span>
                                        <span>数字 (0-9)</span>
                                    </div>
                                    <div class="flex items-center gap-1.5 px-2 py-1 rounded" :class="passwordPolicyResult.hasSpecial ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200/70 text-gray-500'">
                                        <span>{{ passwordPolicyResult.hasSpecial ? '✓' : '○' }}</span>
                                        <span>特殊符号</span>
                                    </div>
                                </div>
                                <div class="text-[11px] text-gray-500">
                                    字符类别达成：<strong class="text-gray-700">{{ passwordPolicyResult.categoryCount }} / 4</strong> 类（至少需要达成 3 类）
                                </div>
                            </div>

                            <div class="flex items-center justify-between pt-1">
                                <span class="text-[11px] text-amber-600 flex items-center gap-1">
                                    <span>⚠</span>
                                    <span>修改密码后需重新登录（现有 API Key 依然有效）</span>
                                </span>
                                <button 
                                    type="submit" 
                                    :disabled="loadingPassword || !newPassword || !passwordPolicyResult.valid || newPassword !== confirmPassword"
                                    class="py-2 px-4 border border-transparent rounded-lg shadow-xs text-xs sm:text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                                >
                                    {{ loadingPassword ? '提交中...' : '确认修改密码' }}
                                </button>
                            </div>
                        </form>
                    </div>

                    <!-- Google 身份验证器两步验证 (2FA) -->
                    <div class="p-5 border border-gray-200/80 rounded-xl bg-white shadow-2xs space-y-4">
                        <div class="flex items-start justify-between gap-3">
                            <div class="space-y-1">
                                <div class="flex items-center gap-2">
                                    <h3 class="text-sm font-bold text-gray-800 flex items-center gap-2">
                                        <span>🛡</span>
                                        <span>Google 两步验证 (2FA)</span>
                                    </h3>
                                    <span 
                                        class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold"
                                        :class="twoFactorEnabled ? 'bg-emerald-100 text-emerald-800 border border-emerald-200' : 'bg-gray-100 text-gray-600'"
                                    >
                                        {{ twoFactorEnabled ? '已启用' : '未开启' }}
                                    </span>
                                </div>
                                <p class="text-xs text-gray-500 leading-relaxed max-w-lg">
                                    {{ twoFactorEnabled 
                                        ? '已为账号开启双重保护。使用账号密码登录时，需输入 Google Authenticator 等应用生成的 6 位动态验证码。' 
                                        : '开启后，在使用账号密码登录时将要求扫码配合 Google 身份验证器输入 6 位动态码，极大提升账户安全性。' 
                                    }}
                                </p>
                            </div>
                            <button
                                v-if="!twoFactorEnabled"
                                type="button"
                                @click="openSetupModal"
                                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs sm:text-sm font-bold rounded-lg shadow-xs transition-all flex-shrink-0 cursor-pointer"
                            >
                                立即开启
                            </button>
                            <button
                                v-else
                                type="button"
                                @click="openDisableModal"
                                class="px-4 py-2 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 text-xs sm:text-sm font-medium rounded-lg transition-all flex-shrink-0 cursor-pointer"
                            >
                                关闭验证
                            </button>
                        </div>
                    </div>
                </div>

                <!-- 3. 云端浏览器环境 -->
                <div v-else-if="infoSubTab === 'browser'" class="max-w-2xl space-y-6">
                    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-3">
                        <div>
                            <h2 class="text-lg font-bold text-gray-900">云端浏览器缓存</h2>
                            <p class="mt-1 text-xs text-gray-500">智能体在执行自动化网页操作任务时所保留的云端浏览器缓存与会话状态。</p>
                        </div>
                        <span v-if="loadingCacheSize" class="text-xs text-gray-400 flex items-center gap-1">
                            <svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                            </svg>
                            正在计算空间占用...
                        </span>
                        <span
                            v-else-if="browserCacheSizeDisplay !== null"
                            :class="[
                                'text-xs font-semibold px-2.5 py-1 rounded-full border',
                                browserCacheSizeBytes === 0
                                    ? 'bg-green-50 text-green-700 border-green-200'
                                    : 'bg-amber-50 text-amber-800 border-amber-200'
                            ]"
                        >
                            已占用 {{ browserCacheSizeDisplay }}
                        </span>
                    </div>

                    <div class="p-5 border border-gray-200/80 rounded-xl bg-white shadow-2xs space-y-4">
                        <div class="space-y-3 text-xs text-gray-600 leading-relaxed">
                            <p class="font-medium text-gray-800">清除浏览器缓存将执行以下清理：</p>
                            <ul class="space-y-2">
                                <li class="flex items-start gap-2">
                                    <span class="text-rose-500 shrink-0">●</span>
                                    <span><strong>Cookie 与第三方网站登录态</strong>：智能体已登录的所有外部网站 Session 将失效，下次执行任务时需重新登录。</span>
                                </li>
                                <li class="flex items-start gap-2">
                                    <span class="text-rose-500 shrink-0">●</span>
                                    <span><strong>浏览历史、页面缓存与 LocalStorage</strong>：临时页面数据与静态缓存一并彻底清空。</span>
                                </li>
                                <li class="flex items-start gap-2">
                                    <span class="text-amber-500 shrink-0">⚠</span>
                                    <span>若当前有智能体正在执行浏览器自动化抓取/点击任务，清除操作将导致其<strong>任务被中断</strong>。</span>
                                </li>
                            </ul>
                            <div class="p-3 bg-emerald-50 text-emerald-800 border border-emerald-100 rounded-lg text-xs">
                                🛡 <strong>安全保证：</strong>此操作仅清理沙箱内部的浏览器临时会话，绝不影响您的本地 Chrome/Edge 浏览器、平台账号会话、对话历史或工作区文件。
                            </div>
                        </div>

                        <div class="pt-2">
                            <button
                                type="button"
                                @click="handleClearBrowserData"
                                :disabled="clearingBrowserData || browserCacheSizeBytes === 0"
                                class="w-full sm:w-auto inline-flex justify-center items-center gap-2 py-2.5 px-5 bg-rose-50 border border-rose-200 text-rose-700 hover:bg-rose-100 text-xs sm:text-sm font-bold rounded-lg shadow-2xs transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                            >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                                {{ clearingBrowserData ? '正在清理中...' : browserCacheSizeBytes === 0 ? '暂无缓存可清除' : '清除云端自动化浏览器缓存与登录态' }}
                            </button>
                        </div>
                    </div>
                </div>
            </main>
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

        <div v-else-if="activeTab === 'mcp'" class="px-3 pb-4 sm:px-6 sm:pb-6 min-h-[28rem]">
            <McpManagement personal-only />
        </div>

        <div v-else-if="activeTab === 'tasks'" class="px-4 pb-4 sm:px-6 sm:pb-6 min-h-[32rem]">
            <TaskCenter personal-only />
        </div>

        <!-- Memory：keep-alive + v-if，离开 Tab 不销毁，保留 memoryView/筛选状态 -->
        <div v-show="activeTab === 'memory'">
            <keep-alive>
                <PersonalMemoryPanel v-if="activeTab === 'memory'" />
            </keep-alive>
        </div>

        </div>

    </div>

    <ConfirmModal
      v-if="showClearBrowserModal"
      title="清除云端自动化浏览器缓存"
      confirmText="确认清除"
      cancelText="取消"
      type="danger"
      :loading="clearingBrowserData"
      @confirm="confirmClearBrowserData"
      @cancel="showClearBrowserModal = false"
    >
      <div class="space-y-3 text-sm">
        <div>
          <p class="font-medium text-gray-700 dark:text-gray-200 mb-1.5">清除后将影响：</p>
          <ul class="space-y-1.5">
            <li class="flex items-start gap-2 text-gray-600 dark:text-gray-300">
              <span class="mt-0.5 shrink-0">🔐</span>
              <span>所有已登录外部网站的 <strong>Cookie 和登录态</strong>（需重新登录）</span>
            </li>
            <li class="flex items-start gap-2 text-gray-600 dark:text-gray-300">
              <span class="mt-0.5 shrink-0">📜</span>
              <span><strong>浏览历史</strong>与页面缓存文件</span>
            </li>
            <li class="flex items-start gap-2 text-amber-600 dark:text-amber-400">
              <span class="mt-0.5 shrink-0">⚠️</span>
              <span>若 AI 正在执行浏览器任务，<strong>任务将立即中断</strong></span>
            </li>
          </ul>
        </div>
        <div class="rounded-lg bg-green-50 dark:bg-green-900/20 px-3 py-2">
          <p class="text-xs text-green-700 dark:text-green-400">
            <strong>✅ 不受影响：</strong>本地 Chrome / Edge 浏览器、平台账号登录、对话记录、工作区文件
          </p>
        </div>
      </div>
    </ConfirmModal>

    <!-- Google 身份验证器 2FA 绑定弹窗 -->
    <div
      v-if="showSetupModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden border border-gray-100 animate-in fade-in zoom-in duration-200">
        <div class="px-6 pt-6 pb-4 border-b border-gray-100 flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center font-bold">
              🔐
            </div>
            <div>
              <h3 class="text-base font-bold text-gray-900">开启 Google 身份验证器两步验证</h3>
              <p class="text-xs text-gray-400">使用移动端身份验证器扫码绑定</p>
            </div>
          </div>
          <button 
            @click="showSetupModal = false"
            class="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6 space-y-5">
          <!-- 步骤一：扫码 -->
          <div>
            <div class="flex items-center gap-2 mb-2.5">
              <span class="w-5 h-5 rounded-full bg-blue-600 text-white text-xs flex items-center justify-center font-bold">1</span>
              <span class="text-xs font-bold text-gray-700">打开 Google Authenticator 扫描下方二维码</span>
            </div>

            <div class="flex flex-col items-center justify-center bg-gray-50 rounded-xl p-4 border border-gray-100">
              <div v-if="loadingSetup" class="h-44 flex flex-col items-center justify-center text-gray-400 gap-2">
                <svg class="w-6 h-6 animate-spin text-blue-600" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                </svg>
                <span class="text-xs">生成安全密钥中...</span>
              </div>
              <template v-else>
                <img 
                  v-if="setupQrDataUrl" 
                  :src="setupQrDataUrl" 
                  alt="Google Authenticator QR Code"
                  class="w-44 h-44 rounded-lg bg-white p-2 shadow-sm border border-gray-200"
                />
                <div v-else class="text-xs text-gray-400 py-6">
                  二维码加载中或无法预览，请直接复制下方密钥手动输入
                </div>

                <div class="mt-3 w-full bg-white px-3 py-2 rounded-lg border border-gray-200 flex items-center justify-between gap-2">
                  <div class="truncate">
                    <span class="text-[10px] text-gray-400 block uppercase font-bold">无法扫码？手动输入密钥</span>
                    <span class="font-mono text-xs font-bold text-gray-800 tracking-wider select-all">{{ setupSecret }}</span>
                  </div>
                  <button
                    type="button"
                    @click="copySecret"
                    class="px-2.5 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold rounded transition-colors flex-shrink-0"
                  >
                    复制
                  </button>
                </div>
              </template>
            </div>
          </div>

          <!-- 步骤二：输入验证码 -->
          <div>
            <div class="flex items-center gap-2 mb-2">
              <span class="w-5 h-5 rounded-full bg-blue-600 text-white text-xs flex items-center justify-center font-bold">2</span>
              <span class="text-xs font-bold text-gray-700">输入应用中显示的 6 位动态验证码</span>
            </div>
            <input
              v-model="setupCode"
              type="text"
              maxlength="6"
              placeholder="000000"
              class="w-full text-center text-2xl font-mono tracking-[0.3em] py-2.5 bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none transition-all placeholder:text-gray-300 font-bold"
              @keyup.enter="handleEnable2FA"
            />
          </div>
        </div>

        <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex items-center justify-end gap-3">
          <button
            type="button"
            @click="showSetupModal = false"
            class="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-xs font-semibold hover:bg-white transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="loadingEnable2FA || !setupCode || setupCode.length !== 6"
            @click="handleEnable2FA"
            class="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold shadow-md hover:shadow-lg transition-all disabled:opacity-50 disabled:shadow-none flex items-center gap-1.5"
          >
            <svg v-if="loadingEnable2FA" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
            {{ loadingEnable2FA ? '验证中...' : '验证并开启' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 关闭两步验证弹窗 -->
    <div
      v-if="showDisableModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden border border-gray-100 animate-in fade-in zoom-in duration-200">
        <div class="px-6 pt-6 pb-4 border-b border-gray-100 flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center font-bold">
              ⚠️
            </div>
            <div>
              <h3 class="text-base font-bold text-gray-900">关闭两步验证</h3>
              <p class="text-xs text-gray-400">关闭后登录仅需密码，安全性将降低</p>
            </div>
          </div>
          <button 
            @click="showDisableModal = false"
            class="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6 space-y-4">
          <div class="flex items-center gap-3 border-b border-gray-100 pb-3">
            <label class="flex items-center gap-1.5 text-xs font-semibold cursor-pointer text-gray-700">
              <input type="radio" v-model="disableType" value="code" class="text-blue-600" />
              <span>验证码确认</span>
            </label>
            <label class="flex items-center gap-1.5 text-xs font-semibold cursor-pointer text-gray-700">
              <input type="radio" v-model="disableType" value="password" class="text-blue-600" />
              <span>当前密码确认</span>
            </label>
          </div>

          <div v-if="disableType === 'code'" class="space-y-2">
            <label class="block text-xs font-bold text-gray-600">Google 身份验证器 6 位动态码</label>
            <input
              v-model="disableCode"
              type="text"
              maxlength="6"
              placeholder="000000"
              class="w-full text-center text-xl font-mono tracking-[0.25em] py-2 bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-rose-500 focus:ring-2 focus:ring-rose-100 outline-none transition-all placeholder:text-gray-300 font-bold"
              @keyup.enter="handleDisable2FA"
            />
          </div>
          <div v-else class="space-y-2">
            <label class="block text-xs font-bold text-gray-600">当前账号登录密码</label>
            <input
              v-model="disablePassword"
              type="password"
              placeholder="请输入当前密码"
              class="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-rose-500 focus:ring-2 focus:ring-rose-100 outline-none transition-all"
              @keyup.enter="handleDisable2FA"
            />
          </div>
        </div>

        <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex items-center justify-end gap-3">
          <button
            type="button"
            @click="showDisableModal = false"
            class="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-xs font-semibold hover:bg-white transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="loadingDisable2FA || (disableType === 'code' ? (!disableCode || disableCode.length !== 6) : !disablePassword)"
            @click="handleDisable2FA"
            class="px-5 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold shadow-md hover:shadow-lg transition-all disabled:opacity-50 disabled:shadow-none flex items-center gap-1.5"
          >
            <svg v-if="loadingDisable2FA" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
            {{ loadingDisable2FA ? '处理中...' : '确认关闭' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重置 API Key 安全确认弹窗 -->
    <div
      v-if="showResetKeyModal"
      class="fixed inset-0 bg-black/50 backdrop-blur-xs flex items-center justify-center z-[9990] p-4"
      @click.self="closeResetApiKeyModal"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden border border-gray-100 animate-in fade-in zoom-in-95 duration-200">
        <div class="px-6 py-5 bg-rose-50/60 border-b border-rose-100/80 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-rose-100 flex items-center justify-center text-rose-600">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <div>
              <h3 class="text-base font-bold text-gray-900">重置访问凭证 (API Key)</h3>
              <p class="text-xs text-gray-500">重置后旧凭证将立即永久失效</p>
            </div>
          </div>
          <button @click="closeResetApiKeyModal" class="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-white/60 transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6 space-y-4">
          <div class="p-3 bg-amber-50 border border-amber-200/80 rounded-xl text-xs text-amber-800 leading-relaxed">
            <strong>操作警告：</strong> 重置 API Key 会使之前生成的所有外部调用秘钥立即失效，已配置该 Key 的自动化脚本、应用或 CLI 需同步更换。当前浏览器会话将自动保持在线。
          </div>

          <!-- 若开启二次验证，提供密码与动态码二选一 -->
          <div v-if="twoFactorEnabled" class="space-y-3">
            <label class="block text-xs font-bold text-gray-700">安全验证方式（二选一）：</label>
            <div class="flex items-center gap-3 border-b border-gray-100 pb-3">
              <label class="flex items-center gap-1.5 text-xs font-semibold cursor-pointer text-gray-700">
                <input type="radio" v-model="resetVerifyType" value="password" class="text-rose-600" />
                <span>当前登录密码</span>
              </label>
              <label class="flex items-center gap-1.5 text-xs font-semibold cursor-pointer text-gray-700">
                <input type="radio" v-model="resetVerifyType" value="code" class="text-rose-600" />
                <span>Google 动态验证码 (2FA)</span>
              </label>
            </div>

            <div v-if="resetVerifyType === 'password'" class="space-y-1.5">
              <label class="block text-xs font-bold text-gray-600">当前账号登录密码</label>
              <input
                v-model="resetPassword"
                type="password"
                placeholder="请输入当前登录密码"
                class="w-full px-3.5 py-2.5 text-sm bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-rose-500 focus:ring-2 focus:ring-rose-100 outline-none transition-all"
                @keyup.enter="handleResetApiKey"
              />
            </div>
            <div v-else class="space-y-1.5">
              <label class="block text-xs font-bold text-gray-600">Google 身份验证器 6 位动态码</label>
              <input
                v-model="resetCode"
                type="text"
                maxlength="6"
                placeholder="000000"
                class="w-full text-center text-xl font-mono tracking-[0.25em] py-2 bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-rose-500 focus:ring-2 focus:ring-rose-100 outline-none transition-all font-bold placeholder:text-gray-300"
                @keyup.enter="handleResetApiKey"
              />
            </div>
          </div>

          <!-- 未开启二次验证，仅校验登录密码 -->
          <div v-else class="space-y-1.5">
            <label class="block text-xs font-bold text-gray-700">当前账号登录密码</label>
            <input
              v-model="resetPassword"
              type="password"
              placeholder="请输入当前登录密码进行身份确认"
              class="w-full px-3.5 py-2.5 text-sm bg-gray-50 border border-gray-300 rounded-lg focus:bg-white focus:border-rose-500 focus:ring-2 focus:ring-rose-100 outline-none transition-all"
              @keyup.enter="handleResetApiKey"
            />
          </div>
        </div>

        <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex items-center justify-end gap-3">
          <button
            type="button"
            @click="closeResetApiKeyModal"
            :disabled="loadingResetKey"
            class="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-xs font-semibold hover:bg-white transition-colors cursor-pointer"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="loadingResetKey || (twoFactorEnabled ? (resetVerifyType === 'code' ? (!resetCode || resetCode.trim().length !== 6) : !resetPassword) : !resetPassword)"
            @click="handleResetApiKey"
            class="px-5 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold shadow-md hover:shadow-lg transition-all disabled:opacity-50 disabled:shadow-none flex items-center gap-1.5 cursor-pointer"
          >
            <svg v-if="loadingResetKey" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
            {{ loadingResetKey ? '重置中...' : '确认重置' }}
          </button>
        </div>
      </div>
    </div>

    <Toast 
      v-if="toast.show" 
      :key="toast.key"
      :message="toast.message" 
      :type="toast.type" 
      @close="closeToast" 
    />
</div>
</template>
