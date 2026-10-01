<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import axios from 'axios'
import { useBranding } from '../composables/useBranding'
import { persistUserInfo } from '../utils/userSession'
import MagnifierTitle from '../components/login/MagnifierTitle.vue'
import BrowserUpgradeModal from '../components/login/BrowserUpgradeModal.vue'
import { detectBrowser, isBrowserUpgradeDismissed, dismissBrowserUpgrade } from '../utils/browserDetect'
import type { BrowserDetectResult } from '../utils/browserDetect'

const router = useRouter()
const route = useRoute()
const { branding, loadBranding } = useBranding()
const activeTab = ref<'sso' | 'password' | 'apikey'>('password')
const ssoEnabled = ref(false)
const apiKey = ref('')
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

const DEFAULT_LOGIN_BRAND_NAME = 'NanZi · 智能体平台'
const DEFAULT_LOGIN_SUBTITLE = 'Your Intelligent Agent Platform'

const productName = computed(() => {
    const configured = branding.value.product_name?.trim()
    return branding.value.enabled && configured ? configured : DEFAULT_LOGIN_BRAND_NAME
})
const loginSubtitle = computed(() => {
    const configured = branding.value.login_subtitle?.trim()
    return branding.value.enabled && configured ? configured : DEFAULT_LOGIN_SUBTITLE
})
const iconUrl = computed(() => branding.value.icon_url)
const showCopyright = computed(() => branding.value.enabled && !!(branding.value.copyright_text || '').trim())
const copyrightText = computed(() => (branding.value.copyright_text || '').trim())
const showSsoFromBranding = computed(() => !branding.value.hide_login_sso)

// Carousel Logic
const currentSlide = ref(0)
type PauseReason = 'visual-hover' | 'form-focus'
const pauseReasons = reactive(new Set<PauseReason>())
const reducedMotion = ref(false)
const mouseLightX = ref(50)
const mouseLightY = ref(50)
const slides = [
    {
        key: 'c',
        title: '一个入口，连接所有智能',
        subtitle: 'One entrance to every intelligent capability',
        desc: '连接智能体、工具与知识，形成可协作、可扩展的智能生态。',
        features: ['Agents', 'Tools', 'Knowledge'],
        gradient: 'from-violet-600/30 via-violet-950/60 to-[#0d0617]',
        accent: 'text-violet-400',
        glow: 'bg-violet-500/10',
        bg: 'bg-[#0d0617]',
        light: false,
    },
    {
        key: 'b',
        title: '从自然语言到可执行结果',
        subtitle: 'Natural language into executable outcomes',
        desc: '轻盈、可信、企业友好。把产品能力放进真实工作流的入口。',
        features: ['ChatBI', 'Knowledge', 'MCP'],
        gradient: 'from-blue-500/30 via-blue-950/60 to-[#0b1830]',
        accent: 'text-blue-300',
        glow: 'bg-cyan-400/10',
        bg: 'bg-[#0b1830]',
        light: false,
    },
    {
        key: 'a',
        title: '智能体，第二操作系统',
        subtitle: 'The second operating system for your organization',
        desc: '开放连接模型、知识与工具，让每一次对话都能落到真实业务。',
        features: ['开放', '智能', '可控'],
        gradient: 'from-blue-600/30 via-blue-950/60 to-[#020617]',
        accent: 'text-blue-400',
        glow: 'bg-blue-500/10',
        bg: 'bg-[#020617]',
        light: false,
    }
]

let slideTimer: ReturnType<typeof setInterval> | null = null
let motionMediaQuery: MediaQueryList | null = null
const sessionStartKey = 'nanzi.login.visual.initial-slide'

const clearSlideTimer = () => {
    if (slideTimer) clearInterval(slideTimer)
    slideTimer = null
}

const restartSlideTimer = () => {
    clearSlideTimer()
    if (reducedMotion.value || pauseReasons.size > 0) return
    slideTimer = setInterval(() => {
        currentSlide.value = (currentSlide.value + 1) % slides.length
    }, 7000)
}

const getInitialSlide = () => {
    try {
        const stored = window.sessionStorage.getItem(sessionStartKey)
        const parsed = stored === null ? NaN : Number(stored)
        if (Number.isInteger(parsed) && parsed >= 0 && parsed < slides.length) return parsed
        const random = Math.random()
        const next = random < 0.5 ? 0 : random < 0.75 ? 1 : 2
        window.sessionStorage.setItem(sessionStartKey, String(next))
        return next
    } catch {
        const random = Math.random()
        return random < 0.5 ? 0 : random < 0.75 ? 1 : 2
    }
}

const setPauseReason = (reason: PauseReason, paused: boolean) => {
    if (paused) pauseReasons.add(reason)
    else pauseReasons.delete(reason)
    restartSlideTimer()
}

const pauseSlideTimer = (reason: PauseReason = 'visual-hover') => setPauseReason(reason, true)
const resumeSlideTimer = (reason: PauseReason = 'visual-hover') => setPauseReason(reason, false)

const resetVisualLight = () => {
    mouseLightX.value = 50
    mouseLightY.value = 50
}

const handleVisualMouseMove = (event: MouseEvent) => {
    if (reducedMotion.value) return
    const panel = event.currentTarget as HTMLElement | null
    if (!panel) return
    const rect = panel.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    mouseLightX.value = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100))
    mouseLightY.value = Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100))
}

const handleVisualMouseLeave = () => {
    resumeSlideTimer('visual-hover')
    resetVisualLight()
}

const selectSlide = (index: number) => {
    if (index < 0 || index >= slides.length) return
    currentSlide.value = index
    restartSlideTimer()
}

const handleMotionPreferenceChange = (event: MediaQueryListEvent) => {
    reducedMotion.value = event.matches
    restartSlideTimer()
}

// Clear form when switching tabs
watch(activeTab, () => {
    username.value = ''
    password.value = ''
    apiKey.value = ''
    error.value = ''
    isTwoFactorStep.value = false
    twoFactorCode.value = ''
    twoFactorToken.value = ''
    // 切 Tab 等价于离开注册流程，避免「SSO 登录」Tab 下还停在注册表单
    isRegisterStep.value = false
    registerSuccess.value = false
    registerError.value = ''
    registerUserNameError.value = ''
})

const isTwoFactorStep = ref(false)
const twoFactorToken = ref('')
const twoFactorUsername = ref('')
const twoFactorCode = ref('')

const handleLoginSuccess = (userData: any) => {
    persistUserInfo(userData)
    // 凭据由后端下发的 HttpOnly Cookie 承载，不再写入 localStorage：
    // 落在 localStorage 的只是 JS 可读的副本，任何一次 XSS 都能带走它。
    
    // 普通业务用户进入个人工作台；管理员保留平台概览入口。
    const returnPath = typeof route.query.next === 'string'
      && route.query.next.startsWith('/')
      && !route.query.next.startsWith('//')
      ? route.query.next
      : ''
    if (returnPath) {
      // OAuth 授权端点由后端处理；必须整页请求，不能只让 SPA 改地址。
      if (returnPath.startsWith('/oauth/authorize')) {
        window.location.assign(returnPath)
      } else {
        router.push(returnPath)
      }
    } else if (userData.role !== 'admin') {
      router.push('/dashboard/workbench')
    } else {
      router.push('/dashboard')
    }
}

const cancelTwoFactor = () => {
    isTwoFactorStep.value = false
    twoFactorToken.value = ''
    twoFactorCode.value = ''
    error.value = ''
}

const handleTwoFactorLogin = async () => {
    if (!twoFactorCode.value || twoFactorCode.value.trim().length !== 6) {
        error.value = '请输入 6 位 Google 动态验证码'
        return
    }
    loading.value = true
    error.value = ''
    try {
        const response = await axios.post('/api/portal/auth/login/2fa', {
            two_factor_token: twoFactorToken.value,
            code: twoFactorCode.value.trim()
        })
        if (response.data?.status === 'success') {
            handleLoginSuccess(response.data.data)
        }
    } catch (e: any) {
        console.error('2FA Login Error:', e)
        const serverDetail = e.response?.data?.detail
        error.value = typeof serverDetail === 'string' ? serverDetail : '动态验证码错误或已过期，请重试'
    } finally {
        loading.value = false
    }
}

const hideLoginApiKey = ref(false)

// ---------------------------------------------------------------------------
// 账号自主注册申请
//
// 入口显隐由系统配置 user_registration_enabled 控制（默认关闭），经公开配置接口下发。
// 前端校验只做体验与「少一次无效请求」，判定权完全在后端：账号名格式、等保复杂度、
// 重名均由 /api/portal/auth/register 裁决，前端不复制一套权威规则。
// ---------------------------------------------------------------------------
const registrationEnabled = ref(false)

// 忘记密码入口。与注册入口同口径：由后端公开配置下发的 password_reset_available 控制，
// 后端只有在「全局邮件服务可用」且「download_url_prefix 已配置」时才置为 true ——
// 两者缺一都会让重置邮件里的链接变成点不开的地址，因此宁可整块入口不显示。
const passwordResetAvailable = ref(false)
const isRegisterStep = ref(false)
const registerSuccess = ref(false)
const registerLoading = ref(false)
const registerError = ref('')
const registerUserNameError = ref('')
const registerForm = reactive({
    user_name: '',
    real_name: '',
    email: '',
    password: '',
    confirmPassword: '',
    remark: '',
})

//: 与后端 USER_NAME_PATTERN 一致：3–32 位、字母开头、仅字母/数字/下划线/中划线/点
const USER_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{2,31}$/
const USER_NAME_FORMAT_HINT = '账号名需为 3–32 位、以字母开头，仅可包含字母、数字、下划线、中划线与点'
const PASSWORD_RULE_HINT = '8–32 位，须包含大写字母、小写字母、数字、特殊符号中的至少 3 种，且不能包含账号名'
const REAL_NAME_MAX_LENGTH = 50
const REMARK_MAX_LENGTH = 255

// ---------------------------------------------------------------------------
// 邮箱格式提示（纯本地）
//
// 与后端 AuthService._EMAIL_RE / EMAIL_MAX_LENGTH 保持一致，只做「明显写错」的
// 即时提示。邮箱全局唯一，**不做输入即查的可用性预检**：那等于把「某邮箱是否已注册」
// 做成公开探测接口，会变成用户枚举面。唯一性与最终格式一律由提交时的后端裁决。
// ---------------------------------------------------------------------------
const EMAIL_MAX_LENGTH = 254
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const registerEmailFormatError = computed(() => {
    const email = registerForm.email.trim()
    if (!email) return ''
    if (email.length > EMAIL_MAX_LENGTH || !EMAIL_PATTERN.test(email)) return '邮箱格式不正确，请检查后重试'
    return ''
})

// ---------------------------------------------------------------------------
// 账号名可用性预检（边输边查）
//
// 后端 GET /api/portal/auth/register/available 是权威判定；这里只是把结论尽早告诉用户，
// 免得辛苦填完五个字段、点了提交才发现账号名被占用。提交时的后端校验依然保留。
// 该接口有独立且更严的 IP 限流（60/小时）：一旦命中 429，本次会话就停止预检、
// 静默退回「提交时校验」，绝不因为一个体验优化把用户挡在注册之外。
// ---------------------------------------------------------------------------
type NameCheckState = 'idle' | 'checking' | 'available' | 'taken' | 'invalid' | 'error'
const nameCheckState = ref<NameCheckState>('idle')
const nameCheckMessage = ref('')
const nameCheckDisabled = ref(false)
const NAME_CHECK_DEBOUNCE_MS = 450
let nameCheckTimer: ReturnType<typeof setTimeout> | null = null
// 请求序号：输入变化后，在途的旧响应直接丢弃，避免「慢响应覆盖新结论」
let nameCheckSeq = 0

const NAME_CHECK_COLORS: Record<NameCheckState, string> = {
    idle: 'text-slate-400',
    checking: 'text-slate-400',
    available: 'text-emerald-600',
    taken: 'text-red-600',
    invalid: 'text-amber-600',
    error: 'text-slate-400',
}
const nameCheckColorClass = computed(() => NAME_CHECK_COLORS[nameCheckState.value])

const resetNameCheck = () => {
    if (nameCheckTimer) {
        clearTimeout(nameCheckTimer)
        nameCheckTimer = null
    }
    nameCheckSeq++
    nameCheckState.value = 'idle'
    nameCheckMessage.value = ''
}

const runNameCheck = async (userName: string) => {
    if (nameCheckDisabled.value) return
    const seq = ++nameCheckSeq
    nameCheckState.value = 'checking'
    nameCheckMessage.value = '正在校验账号名…'
    try {
        const response = await axios.get('/api/portal/auth/register/available', {
            params: { user_name: userName },
        })
        if (seq !== nameCheckSeq) return
        const data = response.data?.data
        if (data?.available) {
            nameCheckState.value = 'available'
            nameCheckMessage.value = data.message || '该账号名可以使用'
        } else {
            nameCheckState.value = data?.reason === 'taken' ? 'taken' : 'invalid'
            nameCheckMessage.value = data?.message || USER_NAME_FORMAT_HINT
        }
    } catch (e: any) {
        if (seq !== nameCheckSeq) return
        if (e.response?.status === 429) {
            // 预检额度用尽：静默降级，交给提交时的后端校验兜底
            nameCheckDisabled.value = true
            nameCheckState.value = 'idle'
            nameCheckMessage.value = ''
            return
        }
        // 网络异常同样静默：不能因为查不了就阻止用户提交
        nameCheckState.value = 'error'
        nameCheckMessage.value = ''
    }
}

const onNameInput = () => {
    registerUserNameError.value = ''
    if (nameCheckTimer) {
        clearTimeout(nameCheckTimer)
        nameCheckTimer = null
    }
    const name = registerForm.user_name.trim()
    if (!name) {
        resetNameCheck()
        return
    }
    // 格式问题本地即时判定，不必等防抖、也不必花一次网络往返
    if (!USER_NAME_PATTERN.test(name)) {
        nameCheckSeq++ // 让在途响应失效
        nameCheckState.value = 'invalid'
        nameCheckMessage.value = USER_NAME_FORMAT_HINT
        return
    }
    nameCheckState.value = 'checking'
    nameCheckMessage.value = '正在校验账号名…'
    nameCheckTimer = setTimeout(() => {
        nameCheckTimer = null
        void runNameCheck(name)
    }, NAME_CHECK_DEBOUNCE_MS)
}

/** 失焦立即查一次：用户填完账号名去点别的字段时不该还在等防抖。 */
const onNameBlur = () => {
    if (nameCheckTimer) {
        clearTimeout(nameCheckTimer)
        nameCheckTimer = null
    }
    const name = registerForm.user_name.trim()
    if (
        name &&
        USER_NAME_PATTERN.test(name) &&
        !nameCheckDisabled.value &&
        nameCheckState.value !== 'available' &&
        nameCheckState.value !== 'taken'
    ) {
        void runNameCheck(name)
    }
}

const resetRegisterForm = () => {
    resetNameCheck()
    registerForm.user_name = ''
    registerForm.real_name = ''
    registerForm.email = ''
    registerForm.password = ''
    registerForm.confirmPassword = ''
    registerForm.remark = ''
}

const openRegister = () => {
    resetRegisterForm()
    registerSuccess.value = false
    registerError.value = ''
    registerUserNameError.value = ''
    isRegisterStep.value = true
}

const backToLogin = () => {
    isRegisterStep.value = false
    registerSuccess.value = false
    registerError.value = ''
    registerUserNameError.value = ''
    resetRegisterForm()
}

/** 返回第一条前端可判定的错误；空串表示可以提交给后端。 */
const validateRegisterForm = (): string => {
    registerUserNameError.value = ''
    const name = registerForm.user_name.trim()
    if (!name) {
        registerUserNameError.value = '请填写账号名'
        return registerUserNameError.value
    }
    if (!USER_NAME_PATTERN.test(name)) {
        registerUserNameError.value = USER_NAME_FORMAT_HINT
        return registerUserNameError.value
    }
    // 预检已判定被占用：直接拦下，不必再等一次网络往返。
    // 结论可能过期（期间被别人注册），所以后端提交时的重名校验依然保留。
    if (nameCheckState.value === 'taken') {
        registerUserNameError.value = nameCheckMessage.value || '该账号名已被占用，请更换后重试'
        return registerUserNameError.value
    }
    const realName = registerForm.real_name.trim()
    if (!realName) return '请填写用户姓名'
    if (realName.length > REAL_NAME_MAX_LENGTH) return `用户姓名不超过 ${REAL_NAME_MAX_LENGTH} 字`
    if (registerEmailFormatError.value) return registerEmailFormatError.value
    if (!registerForm.password) return '请填写密码'
    if (!registerForm.confirmPassword) return '请再次输入密码'
    if (registerForm.password !== registerForm.confirmPassword) return '两次输入的密码不一致'
    if (registerForm.remark.length > REMARK_MAX_LENGTH) return `备注不超过 ${REMARK_MAX_LENGTH} 字`
    return ''
}

const handleRegister = async () => {
    registerError.value = ''
    const localError = validateRegisterForm()
    if (localError) {
        registerError.value = localError
        return
    }
    registerLoading.value = true
    try {
        const response = await axios.post('/api/portal/auth/register', {
            user_name: registerForm.user_name.trim(),
            real_name: registerForm.real_name.trim(),
            email: registerForm.email.trim() || undefined,
            password: registerForm.password,
            remark: registerForm.remark.trim() || null,
        })
        if (response.data?.status === 'success') {
            registerSuccess.value = true
            registerForm.password = ''
            registerForm.confirmPassword = ''
        }
    } catch (e: any) {
        const detail = e.response?.data?.detail
        const message = typeof detail === 'string' ? detail : '提交失败，请稍后重试'
        // 重名是字段级问题：挂到账号名输入框下方，用户一眼知道改哪个字段
        if (message.includes('已被占用')) {
            registerUserNameError.value = message
            // 同步预检状态，避免下方同时出现「可以使用」的旧结论
            nameCheckState.value = 'taken'
            nameCheckMessage.value = message
        } else {
            // 邮箱（格式非法 / 已被其他账号使用）等其余 400 统一走表单级提示
            registerError.value = message
        }
    } finally {
        registerLoading.value = false
    }
}

const panelTitle = computed(() => {
    if (isTwoFactorStep.value) return '两步验证'
    if (isRegisterStep.value) return registerSuccess.value ? '申请已提交' : '申请账号'
    return '欢迎回来'
})
const panelSubtitle = computed(() => {
    if (isTwoFactorStep.value) return '该账号已启用两步验证保护，请输入动态验证码'
    if (isRegisterStep.value) {
        return registerSuccess.value
            ? '管理员审核通过后即可使用该账号登录'
            : '填写信息提交注册申请，需管理员审核通过后才能登录'
    }
    return '请输入您的凭据以访问控制台'
})

const fetchPublicConfig = async () => {
    try {
        const response = await axios.get('/api/portal/auth/config/public')
        if (response.data?.status === 'success') {
            ssoEnabled.value = response.data.data?.yovole_sso_enabled === true
            hideLoginApiKey.value = response.data.data?.hide_login_apikey === true
            registrationEnabled.value = response.data.data?.user_registration_enabled === true
            passwordResetAvailable.value = response.data.data?.password_reset_available === true
            const tz = response.data.data?.platform_timezone
            if (tz) {
                const { setPlatformTimezone } = await import('@/utils/platformTimezone')
                setPlatformTimezone(tz)
            }
            if (hideLoginApiKey.value && activeTab.value === 'apikey') {
                activeTab.value = 'password'
            }
            if (ssoEnabled.value && !branding.value.hide_login_sso) {
                activeTab.value = 'sso'
            }
        }
    } catch (e) {
        console.error('获取公开配置失败:', e)
    }
}

const showBrowserUpgradeModal = ref(false)
const browserDetectResult = ref<BrowserDetectResult>({
    name: '',
    version: '',
    majorVersion: 0,
    isSupported: true,
    isLowVersion: false,
    recommendation: '',
    downloadUrl: ''
})

const checkBrowserVersion = () => {
    const result = detectBrowser()
    browserDetectResult.value = result
    if (result.isLowVersion && !isBrowserUpgradeDismissed()) {
        showBrowserUpgradeModal.value = true
    }
}

const handleDismissBrowserUpgrade = () => {
    dismissBrowserUpgrade()
    showBrowserUpgradeModal.value = false
}

const handleCloseBrowserUpgrade = () => {
    showBrowserUpgradeModal.value = false
}

onMounted(async () => { 
    currentSlide.value = getInitialSlide()
    motionMediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = motionMediaQuery.matches
    motionMediaQuery.addEventListener?.('change', handleMotionPreferenceChange)
    restartSlideTimer()
    checkBrowserVersion()
    await loadBranding()
    if (branding.value.hide_login_sso && activeTab.value === 'sso') {
        activeTab.value = 'password'
    }
    fetchPublicConfig()
})

watch(() => branding.value.hide_login_sso, (hide) => {
    if (hide && activeTab.value === 'sso') {
        activeTab.value = 'password'
    }
})
onUnmounted(() => { 
    clearSlideTimer()
    motionMediaQuery?.removeEventListener?.('change', handleMotionPreferenceChange)
    resetNameCheck() // 清掉在途的账号名预检，避免组件卸载后回调仍在跑
})

const handleLogin = async () => {
    let payload: any = {}
    let endpoint = '/api/portal/auth/login'
    
    if (activeTab.value === 'apikey') {
        if (!apiKey.value) { error.value = '请提供访问凭证'; return }
        payload = { api_key: apiKey.value }
    } else if (activeTab.value === 'password') {
        if (!username.value || !password.value) { error.value = '请完善账号信息'; return }
        payload = { username: username.value, password: password.value }
    } else if (activeTab.value === 'sso') {
        if (!username.value || !password.value) { error.value = '请完善 SSO 账号信息'; return }
        payload = { username: username.value, password: password.value }
        endpoint = '/api/portal/auth/sso/login'
    } else return

    loading.value = true
    error.value = ''
    try {
        const response = await axios.post(endpoint, payload)
        if (response.data?.status === 'two_factor_required') {
            // 命中两步验证拦截，切换至动态验证码输入流程
            isTwoFactorStep.value = true
            twoFactorToken.value = response.data.data?.two_factor_token || ''
            twoFactorUsername.value = response.data.data?.user_name || username.value
            twoFactorCode.value = ''
            error.value = ''
        } else if (response.data?.status === 'success') {
            handleLoginSuccess(response.data.data)
        }
    } catch (e: any) {
        console.error('Login Error:', e)
        const serverMessage = e.response?.data?.message
        const serverDetail = e.response?.data?.detail
        
        if (serverMessage) {
            error.value = serverMessage
        } else if (serverDetail) {
            error.value = typeof serverDetail === 'string' ? serverDetail : JSON.stringify(serverDetail)
        } else if (e.response?.status) {
            error.value = `服务器错误 (${e.response.status})，请检查日志`
        } else {
            error.value = '网络连接异常，请检查后端服务是否启动'
        }
    } finally { loading.value = false }
}
</script>

<template>
  <div class="login-shell w-screen flex bg-slate-900 font-sans overflow-hidden">
    
    <!-- Left Section: Visuals & Branding (Carousel) -->
    <div
        class="hidden lg:flex flex-1 relative overflow-hidden transition-all duration-1000"
        :class="slides[currentSlide]?.bg || ''"
        @mouseenter="pauseSlideTimer('visual-hover')"
        @mousemove="handleVisualMouseMove"
        @mouseleave="handleVisualMouseLeave"
    >
        <div
            class="absolute top-10 left-10 xl:top-12 xl:left-12 z-30 flex items-center gap-3"
            :class="slides[currentSlide]?.light ? 'text-slate-900' : 'text-white'"
        >
            <img :src="iconUrl" class="w-12 h-12 rounded-2xl object-cover shadow-xl" alt="NanZi logo" />
            <div class="text-left">
                <div class="text-base font-bold tracking-tight">{{ productName }}</div>
                <div class="text-[10px] tracking-[0.16em] uppercase" :class="slides[currentSlide]?.light ? 'text-slate-500' : 'text-slate-300/80'">{{ loginSubtitle }}</div>
            </div>
        </div>
        <div
            aria-hidden="true"
            class="absolute inset-0 bg-[radial-gradient(circle_at_30%_30%,_var(--tw-gradient-stops))] transition-all duration-1000 scale-150"
            :class="slides[currentSlide]?.gradient || ''"
        ></div>
        <div
            aria-hidden="true"
            class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[1000px] h-[1000px] rounded-full blur-[180px] animate-pulse transition-all duration-1000 opacity-20"
            :class="slides[currentSlide]?.glow || ''"
        ></div>
        <div
            aria-hidden="true"
            class="absolute inset-0 opacity-[0.04]"
            style="background-image: linear-gradient(#3b82f6 1px, transparent 1px), linear-gradient(90deg, #3b82f6 1px, transparent 1px); background-size: 60px 60px;"
        ></div>
        <div
            aria-hidden="true"
            class="pointer-events-none absolute z-0 h-[360px] w-[360px] -translate-x-1/2 -translate-y-1/2 rounded-full blur-[72px] transition-[left,top] duration-300 ease-out motion-reduce:transition-none"
            :style="{ left: `${mouseLightX}%`, top: `${mouseLightY}%`, background: 'radial-gradient(circle, rgba(96, 165, 250, 0.24) 0%, rgba(34, 211, 238, 0.08) 42%, transparent 72%)' }"
        ></div>

        <div class="relative w-full h-full flex items-center justify-center">
            <div
                v-for="(slide, index) in slides"
                :key="slide.key"
                class="absolute inset-0 flex flex-col items-center justify-center text-center px-12 xl:px-20 transition-all duration-700 ease-in-out motion-reduce:transition-none"
                :class="currentSlide === index ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-8 pointer-events-none'"
                :aria-hidden="currentSlide !== index"
            >
                <div class="relative z-10 max-w-4xl xl:max-w-[1100px]">
                    <MagnifierTitle
                        :text="slide.title"
                        :tone="slide.light ? 'light' : 'dark'"
                        :active="currentSlide === index"
                        class="mb-4"
                    />
                    <p class="text-xl xl:text-2xl font-light tracking-[0.05em] mb-4" :class="slide.light ? 'text-slate-600' : 'text-slate-300'">
                        {{ slide.subtitle }}
                    </p>
                    <p class="text-sm tracking-[0.12em] mb-12 opacity-80" :class="slide.light ? 'text-slate-500' : 'text-slate-400'">
                        {{ slide.desc }}
                    </p>

                    <div class="flex items-center justify-center gap-7 xl:gap-12">
                        <template v-for="(feature, fIndex) in slide.features" :key="feature">
                            <div class="flex flex-col items-center gap-2">
                                <span class="font-bold text-lg tracking-widest transition-colors duration-500" :class="slide.accent">{{ feature }}</span>
                                <span class="text-[10px] uppercase font-mono tracking-tighter" :class="slide.light ? 'text-slate-400' : 'text-slate-500'">Capability 0{{ fIndex + 1 }}</span>
                            </div>
                            <div v-if="fIndex < slide.features.length - 1" class="w-px h-8" :class="slide.light ? 'bg-slate-300' : 'bg-slate-700'"></div>
                        </template>
                    </div>
                </div>
            </div>
        </div>

        <div class="absolute bottom-24 left-1/2 -translate-x-1/2 flex gap-3 z-30" role="group" aria-label="登录页主视觉章节">
            <button
                v-for="(slide, index) in slides"
                :key="slide.key"
                type="button"
                :aria-label="`切换到${slide.title}主视觉`"
                :aria-current="currentSlide === index ? 'true' : undefined"
                @click="selectSlide(index)"
                class="h-1.5 transition-all duration-500 rounded-full motion-reduce:transition-none"
                :class="currentSlide === index ? 'w-10 bg-blue-400 shadow-[0_0_10px_rgba(96,165,250,0.7)]' : 'w-4 bg-slate-500/50 hover:bg-slate-300'"
            ></button>
        </div>

        <div aria-hidden="true" class="absolute bottom-10 left-12 flex items-center gap-4 opacity-40">
            <div class="flex items-center gap-2">
                <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                <span class="text-[10px] text-slate-400 font-mono tracking-widest uppercase">System Operational</span>
            </div>
            <span class="text-slate-700">|</span>
            <span class="text-[10px] text-slate-400 font-mono tracking-widest uppercase">Nodes: 0x081</span>
        </div>
    </div>

    <!-- Right Section: Compact Login Panel -->
    <!-- min-h-0 + overflow-y-auto：移动端注册表单（5 个字段）高于视口时，
         必须能在面板内部滚动。父容器是 overflow-hidden 的 h-[100dvh]，
         少了 min-h-0 这个 flex 子项会被内容撑高、再被父级裁掉，出现「滚动条都没有」。 -->
    <div
        class="w-full lg:w-[420px] xl:w-[460px] min-h-0 overflow-y-auto flex flex-col bg-white relative shadow-2xl z-20"
        @focusin="pauseSlideTimer('form-focus')"
        @focusout="resumeSlideTimer('form-focus')"
    >
        <!-- Top accent -->
        <div class="h-1 w-full bg-blue-600 shrink-0"></div>

        <!-- Mobile Header (Visible only on small screens) -->
        <div class="lg:hidden pt-8 px-6 pb-0 animate-fade-in shrink-0">
            <div class="flex items-center gap-3 mb-2">
                <img :src="iconUrl" class="w-8 h-8 rounded-lg drop-shadow-md object-cover" alt="Logo" />
                <h1 class="text-xl font-bold text-slate-900 tracking-tight">{{ productName }}</h1>
            </div>
            <p class="text-xs text-slate-500 tracking-wide uppercase">{{ loginSubtitle }}</p>
        </div>

        <!-- 移动端从顶部排布（lg 起才垂直居中）：justify-center 在内容高于容器时
             会把顶部推到不可达区域，滚动条也救不回来。
             （lg:justify-center 放在末尾是为了不打断登录页布局契约里
             「px-6 lg:px-7 xl:px-10」这条连续子串断言，Tailwind 类顺序不影响产物。） -->
        <div class="flex-1 flex flex-col px-6 lg:px-7 xl:px-10 py-6 xl:py-0 lg:justify-center">
            <div class="mb-6 xl:mb-10">
                <h2 class="text-2xl font-bold text-slate-900 tracking-tight">
                    {{ panelTitle }}
                </h2>
                <p class="text-slate-400 text-xs mt-1">
                    {{ panelSubtitle }}
                </p>
            </div>

            <!-- 常规登录 Tabs (仅在非 2FA、非注册阶段展示) -->
            <div v-if="!isTwoFactorStep && !isRegisterStep" class="flex space-x-6 xl:space-x-8 border-b border-slate-100 mb-5 xl:mb-8">
                <button 
                    v-for="tab in [{id:'sso', name:'SSO 登录'}, {id:'password', name:'本地账号'}, {id:'apikey', name:'API Key'}].filter(t => (t.id !== 'sso' || (ssoEnabled && showSsoFromBranding)) && (t.id !== 'apikey' || !hideLoginApiKey))" 
                    :key="tab.id"
                    @click="activeTab = tab.id as any"
                    class="pb-3 text-sm font-semibold transition-all relative"
                    :class="activeTab === tab.id ? 'text-blue-600' : 'text-slate-400 hover:text-slate-600'"
                >
                    {{ tab.name }}
                    <div v-if="activeTab === tab.id" class="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-full"></div>
                </button>
            </div>

            <!-- 常规登录表单 -->
            <form v-if="!isTwoFactorStep && !isRegisterStep" @submit.prevent="handleLogin" class="space-y-4 xl:space-y-6">
                <div class="space-y-4">
                    <div v-if="activeTab === 'password' || activeTab === 'sso'" class="space-y-4 animate-fade-slide-up">
                        <div class="space-y-1.5">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">
                                {{ activeTab === 'sso' ? 'SSO 用户名' : '本地账号用户名' }}
                            </label>
                            <input 
                                v-model="username" 
                                type="text" 
                                class="w-full bg-white border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100"
                                :placeholder="activeTab === 'sso' ? '请输入 YES 账号' : '请输入本地账号用户名'"
                            />
                        </div>
                        <div class="space-y-1.5">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">密码</label>
                            <input 
                                v-model="password" 
                                type="password" 
                                class="w-full bg-white border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100"
                                placeholder="••••••••"
                            />
                        </div>
                        <div v-if="activeTab === 'sso'" class="flex items-center gap-1.5 ml-1 animate-fade-in">
                            <div class="w-1 h-1 rounded-full bg-blue-500"></div>
                            <span class="text-[10px] text-slate-400 font-medium">提示：请使用 YES 账号密码登录</span>
                        </div>
                    </div>

                    <div v-if="activeTab === 'apikey'" class="animate-fade-slide-up">
                        <div class="space-y-1.5">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">API Key (X-API-Key)</label>
                            <textarea 
                                v-model="apiKey" 
                                rows="3"
                                class="w-full bg-white border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all resize-none font-mono disabled:bg-gray-100"
                                placeholder="ys_..."
                            ></textarea>
                        </div>
                    </div>
                </div>

                <div v-if="error" class="p-3 bg-red-50 text-red-600 text-[11px] rounded-lg flex items-center gap-2 animate-shake">
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span>认证失败: {{ error }}</span>
                </div>

                <button 
                    type="submit" 
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 text-sm font-bold shadow-lg shadow-blue-600/10 transition-all active:scale-[0.98] disabled:opacity-70 flex justify-center items-center"
                    :disabled="loading"
                >
                    <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-3"></span>
                    {{ loading ? '连接中...' : (activeTab === 'sso' ? '统一认证登录' : '进入平台 / LOGIN') }}
                </button>

                <!-- 底部辅助入口：注册申请与忘记密码。
                     两者各自独立受配置控制（user_registration_enabled / password_reset_available），
                     可能只出现其中一个；因此用「居中 + 细分隔符」而不是左右分列——
                     分列在只剩一个入口时会把它贴到边线上。 -->
                <div
                    v-if="activeTab === 'password' && (registrationEnabled || passwordResetAvailable)"
                    class="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 pt-1 text-xs"
                >
                    <span v-if="registrationEnabled" class="flex items-center">
                        <span class="text-slate-400">还没有账号？</span>
                        <button
                            type="button"
                            @click="openRegister"
                            class="text-xs font-semibold text-blue-600 hover:text-blue-700 transition-colors ml-1"
                        >
                            申请账号
                        </button>
                    </span>

                    <!-- 分隔符只在两项同时存在时出现，避免出现「孤零零一根竖线」 -->
                    <span
                        v-if="registrationEnabled && passwordResetAvailable"
                        aria-hidden="true"
                        class="h-3 w-px bg-slate-200"
                    ></span>

                    <!-- 忘记密码入口：仅本地账号密码登录时适用（SSO 的密码不归本平台管），
                         且由后端下发的 password_reset_available 控制显隐 -->
                    <router-link
                        v-if="passwordResetAvailable"
                        to="/reset-password"
                        class="text-xs font-semibold text-blue-600 hover:text-blue-700 transition-colors"
                    >
                        忘记密码？
                    </router-link>
                </div>
            </form>

            <!-- 两步验证 (2FA) 动态码输入表单 -->
            <form v-else-if="isTwoFactorStep" @submit.prevent="handleTwoFactorLogin" class="space-y-5 animate-fade-slide-up">
                <div class="p-3.5 bg-blue-50/60 rounded-xl border border-blue-100 flex items-center gap-3">
                    <div class="w-9 h-9 rounded-lg bg-blue-600 text-white flex items-center justify-center font-bold text-base flex-shrink-0 shadow-sm">
                        🔐
                    </div>
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="text-xs font-bold text-slate-800">Google 身份验证器</span>
                            <span class="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded font-mono">2FA</span>
                        </div>
                        <p class="text-[11px] text-slate-500 truncate mt-0.5 font-mono">账号：@{{ twoFactorUsername }}</p>
                    </div>
                </div>

                <div class="space-y-2">
                    <div class="flex items-center justify-between">
                        <label class="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                            6 位动态验证码
                        </label>
                        <span class="text-[10px] text-slate-400">30 秒自动刷新</span>
                    </div>
                    <input 
                        v-model="twoFactorCode"
                        type="text" 
                        maxlength="6"
                        autofocus
                        class="w-full bg-white border border-slate-200 rounded-lg py-3 text-center text-2xl font-mono tracking-[0.35em] text-slate-900 outline-none focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-100 transition-all font-bold placeholder:text-slate-300 placeholder:tracking-widest disabled:bg-gray-100"
                        placeholder="000000"
                    />
                </div>

                <div v-if="error" class="p-3 bg-red-50 text-red-600 text-[11px] rounded-lg flex items-center gap-2 animate-shake">
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span>验证失败: {{ error }}</span>
                </div>

                <button 
                    type="submit" 
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 text-sm font-bold shadow-lg shadow-blue-600/10 transition-all active:scale-[0.98] disabled:opacity-70 flex justify-center items-center"
                    :disabled="loading || !twoFactorCode || twoFactorCode.length !== 6"
                >
                    <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-3"></span>
                    {{ loading ? '验证中...' : '完成验证并进入平台' }}
                </button>

                <div class="text-center pt-2">
                    <button
                        type="button"
                        @click="cancelTwoFactor"
                        class="text-xs text-slate-400 hover:text-slate-600 transition-colors inline-flex items-center gap-1 font-medium"
                    >
                        <span>← 返回修改账号密码</span>
                    </button>
                </div>
            </form>

            <!-- 账号注册申请表单 -->
            <form v-else-if="!registerSuccess" @submit.prevent="handleRegister" class="space-y-4 xl:space-y-5 animate-fade-slide-up">
                <div class="space-y-4">
                    <div class="space-y-1.5">
                        <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">账号名</label>
                        <input
                            v-model="registerForm.user_name"
                            type="text"
                            autocomplete="username"
                            autocapitalize="none"
                            autocorrect="off"
                            spellcheck="false"
                            class="w-full bg-white border rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            :class="
                                registerUserNameError || nameCheckState === 'taken'
                                    ? 'border-red-400 focus:border-red-500'
                                    : nameCheckState === 'available'
                                      ? 'border-emerald-400 focus:border-emerald-500'
                                      : 'border-slate-200 focus:border-blue-500'
                            "
                            placeholder="3–32 位，字母开头"
                            @input="onNameInput"
                            @blur="onNameBlur"
                        />
                        <p v-if="registerUserNameError" class="text-[11px] text-red-600 ml-1">{{ registerUserNameError }}</p>
                        <p v-else-if="nameCheckMessage" class="text-[11px] ml-1 flex items-start gap-1" :class="nameCheckColorClass">
                            <span v-if="nameCheckState === 'available'" aria-hidden="true">✓</span>
                            <span v-else-if="nameCheckState === 'taken' || nameCheckState === 'invalid'" aria-hidden="true">✕</span>
                            <span>{{ nameCheckMessage }}</span>
                        </p>
                    </div>

                    <div class="space-y-1.5">
                        <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">用户姓名</label>
                        <input
                            v-model="registerForm.real_name"
                            type="text"
                            :maxlength="REAL_NAME_MAX_LENGTH"
                            class="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            placeholder="请输入真实姓名"
                        />
                    </div>

                    <div class="space-y-1.5">
                        <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">邮箱（选填，用于接收审核结果通知）</label>
                        <input
                            v-model="registerForm.email"
                            type="email"
                            autocomplete="email"
                            autocapitalize="none"
                            autocorrect="off"
                            spellcheck="false"
                            :maxlength="EMAIL_MAX_LENGTH"
                            class="w-full bg-white border rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            :class="registerEmailFormatError ? 'border-red-400 focus:border-red-500' : 'border-slate-200 focus:border-blue-500'"
                            placeholder="name@example.com"
                        />
                        <p v-if="registerEmailFormatError" class="text-[11px] text-red-600 ml-1">{{ registerEmailFormatError }}</p>
                        <p v-else class="text-[10px] text-slate-400 ml-1 leading-relaxed">审核结果将通过邮件发送到该地址；不填写则仅站内查看</p>
                    </div>

                    <div class="space-y-1.5">
                        <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">密码</label>
                        <input
                            v-model="registerForm.password"
                            type="password"
                            autocomplete="new-password"
                            class="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            placeholder="••••••••"
                        />
                        <p class="text-[10px] text-slate-400 ml-1 leading-relaxed">{{ PASSWORD_RULE_HINT }}</p>
                    </div>

                    <div class="space-y-1.5">
                        <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">确认密码</label>
                        <input
                            v-model="registerForm.confirmPassword"
                            type="password"
                            autocomplete="new-password"
                            class="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            placeholder="请再次输入密码"
                        />
                    </div>

                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between ml-1">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">备注（可选）</label>
                            <span class="text-[10px] text-slate-300 font-mono">{{ registerForm.remark.length }}/{{ REMARK_MAX_LENGTH }}</span>
                        </div>
                        <textarea
                            v-model="registerForm.remark"
                            rows="2"
                            :maxlength="REMARK_MAX_LENGTH"
                            class="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all resize-none disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                            placeholder="可填写所属部门、申请用途等，便于管理员审核"
                        ></textarea>
                    </div>
                </div>

                <div v-if="registerError" class="p-3 bg-red-50 text-red-600 text-[11px] rounded-lg flex items-center gap-2 animate-shake">
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span>{{ registerError }}</span>
                </div>

                <button
                    type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 text-sm font-bold shadow-lg shadow-blue-600/10 transition-all active:scale-[0.98] disabled:opacity-70 flex justify-center items-center"
                    :disabled="registerLoading"
                >
                    <span v-if="registerLoading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-3"></span>
                    {{ registerLoading ? '提交中...' : '提交注册申请' }}
                </button>

                <div class="text-center pt-1">
                    <button
                        type="button"
                        @click="backToLogin"
                        class="text-xs text-slate-400 hover:text-slate-600 transition-colors inline-flex items-center gap-1 font-medium"
                    >
                        <span>← 返回登录</span>
                    </button>
                </div>
            </form>

            <!-- 注册申请提交成功 -->
            <div v-else class="space-y-5 animate-fade-slide-up text-center">
                <div class="mx-auto w-14 h-14 rounded-2xl bg-emerald-50 border border-emerald-100 flex items-center justify-center">
                    <svg class="w-7 h-7 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                </div>
                <div class="space-y-1.5">
                    <p class="text-sm font-bold text-slate-800">注册申请已提交</p>
                    <p class="text-xs text-slate-500 leading-relaxed">
                        请等待管理员审核。审核通过后，即可使用「账号名 / 密码」登录平台。
                    </p>
                </div>
                <button
                    type="button"
                    @click="backToLogin"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 text-sm font-bold shadow-lg shadow-blue-600/10 transition-all active:scale-[0.98]"
                >
                    返回登录
                </button>
            </div>

            <div class="mt-8 pt-5 xl:mt-12 xl:pt-8 border-t border-slate-50 text-center">
                <p class="text-[10px] text-slate-300 tracking-[0.3em] font-light uppercase">
                    Authorized Personnel Only
                </p>
            </div>
        </div>
        
        <div v-if="showCopyright" class="p-6 text-center">
            <p class="login-copyright text-[10px] text-slate-400/80 font-extralight tracking-[0.22em] leading-[1.8] whitespace-pre-line">
                {{ copyrightText }}
            </p>
            <div class="mt-3 mx-auto h-px w-14 bg-gradient-to-r from-transparent via-slate-300/40 to-transparent" aria-hidden="true" />
        </div>
        <div v-else class="p-6 text-center text-[10px] text-slate-400 font-mono opacity-40">
            © 2026 NanZi Network // CLOUD_PIVOT_AGENT
        </div>
    </div>

    <!-- 浏览器低版本/不兼容升级引导弹窗 -->
    <BrowserUpgradeModal
        :show="showBrowserUpgradeModal"
        :detect-result="browserDetectResult"
        @dismiss="handleDismissBrowserUpgrade"
        @close="handleCloseBrowserUpgrade"
    />
  </div>
</template>

<style scoped>
/* 移动端浏览器（iOS Safari / Android Chrome）的 100vh 含被地址栏遮挡的区域，
   会把面板底部裁掉；100dvh 跟随可视视口。不支持的浏览器保留 100vh 兜底。 */
.login-shell { height: 100vh; height: 100dvh; }
.animate-fade-slide-up { animation: fadeSlideUp 0.3s ease-out; }
@keyframes fadeSlideUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.animate-fade-in { animation: fadeIn 0.4s ease-out; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.animate-shake { animation: shake 0.4s cubic-bezier(.36,.07,.19,.97) both; }
@keyframes shake { 10%, 90% { transform: translate3d(-1px, 0, 0); } 20%, 80% { transform: translate3d(2px, 0, 0); } 30%, 50%, 70% { transform: translate3d(-2px, 0, 0); } 40%, 60% { transform: translate3d(2px, 0, 0); } }
</style>
