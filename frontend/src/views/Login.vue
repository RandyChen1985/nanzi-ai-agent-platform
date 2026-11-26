<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, watch, computed, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import axios from 'axios'
import { useBranding } from '../composables/useBranding'
import { persistUserInfo } from '../utils/userSession'
import { clearNavigationSnapshot } from '../composables/useNavigation'
import { applyProModeDefaultTheme, applyUserModeDefaultTheme, resolveInitialUiMode } from '../composables/useUiMode'
import { useLoginTheme } from '../composables/useLoginTheme'
import {
    LOGIN_THEMES,
    LOGIN_THEME_MAP,
    isLoginThemeId,
    type LoginThemeId,
} from '../constants/loginThemes'
import { F3_SATELLITES } from '../utils/loginThemeRenderers'
import LoginThemeBackdrop from '../components/login/LoginThemeBackdrop.vue'
import BrowserUpgradeModal from '../components/login/BrowserUpgradeModal.vue'
import { detectBrowser, isBrowserUpgradeDismissed, dismissBrowserUpgrade } from '../utils/browserDetect'
import type { BrowserDetectResult } from '../utils/browserDetect'

const router = useRouter()
const route = useRoute()
const { branding, loadBranding } = useBranding()
const { loginTheme, setLoginTheme } = useLoginTheme()

// ---------------------------------------------------------------------------
// 8 套登录页视觉主题（默认 f1 · 双面穿界星盘）
// 支持个人中心跳转 `/login?preview=<theme>` 进行全屏沉浸预览与一键应用。
// ---------------------------------------------------------------------------
const previewThemeOverride = ref<LoginThemeId | null>(null)
const isPreviewMode = computed(() => previewThemeOverride.value !== null)
const applyingPreview = ref(false)
const previewNotice = ref('')
const previewNoticeTone = ref<'success' | 'warning' | 'error'>('success')

const syncPreviewFromRoute = () => {
    const raw = route.query.preview
    const candidate = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
    if (isLoginThemeId(candidate)) {
        previewThemeOverride.value = candidate
    } else if (raw !== undefined && raw !== null) {
        previewThemeOverride.value = loginTheme.value
    } else {
        previewThemeOverride.value = null
    }
}

watch(() => route.query.preview, syncPreviewFromRoute, { immediate: true })

const activeThemeId = computed<LoginThemeId>(() => previewThemeOverride.value ?? loginTheme.value)
const activeThemeMeta = computed(() => LOGIN_THEME_MAP[activeThemeId.value])

const selectPreviewTheme = (themeId: LoginThemeId) => {
    previewThemeOverride.value = themeId
    previewNotice.value = ''
    void router.replace({ query: { ...route.query, preview: themeId } })
}

const applyPreviewTheme = async () => {
    if (applyingPreview.value) return
    applyingPreview.value = true
    previewNotice.value = ''
    try {
        const result = await setLoginTheme(activeThemeId.value, { allowLocalFallback: true })
        if (result === 'saved') {
            previewNoticeTone.value = 'success'
            previewNotice.value = '已设为我的登录页风格'
        } else if (result === 'local-only') {
            previewNoticeTone.value = 'warning'
            previewNotice.value = '未登录状态，已在本机生效'
        } else {
            previewNoticeTone.value = 'error'
            previewNotice.value = '保存失败，请稍后重试'
        }
    } finally {
        applyingPreview.value = false
    }
}

const exitPreviewMode = () => {
    if (window.opener && !window.opener.closed) {
        window.close()
        return
    }
    router.push({ path: '/dashboard/personal', query: { tab: 'info', subtab: 'login-theme' } })
}

// 实时指针坐标读数（供星盘方位角、干涉相位、航电姿态仪读数使用）
const pointerCoord = reactive({ x: 0.4, y: 0.5 })
const handleBackdropPointer = (payload: { x: number; y: number }) => {
    pointerCoord.x = payload.x
    pointerCoord.y = payload.y
}

const activeSatelliteIndex = ref(0)
const activeSatellite = computed(() => F3_SATELLITES[activeSatelliteIndex.value] ?? F3_SATELLITES[0])

const liveTelemetryText = computed(() => {
    const id = activeThemeId.value
    const az = (pointerCoord.x * 360).toFixed(1).padStart(5, '0')
    const el = ((0.5 - pointerCoord.y) * 90).toFixed(1)
    const elSigned = Number(el) >= 0 ? `+${el}` : el
    if (id === 'f1') return `AZIMUTH ${az}° · ELEVATION ${elSigned}° · ORBITAL SYNC`
    if (id === 'f2') return `AZIMUTH ${az}° · OPTICS LOCKED`
    if (id === 'f4') return `CHRONO DIAL · ${az}° / ${elSigned}° SYNC`
    if (id === 'h') return `λ = 26.0px · ΔΦ = ${((pointerCoord.x - 0.5) * 6.28).toFixed(2)} rad`
    if (id === 'j') {
        const pitch = ((0.5 - pointerCoord.y) * 24).toFixed(1)
        const roll = ((pointerCoord.x - 0.5) * 30).toFixed(1)
        const hdg = Math.round(pointerCoord.x * 360).toString().padStart(3, '0')
        return `PITCH ${Number(pitch) >= 0 ? '+' : ''}${pitch}° · ROLL ${Number(roll) >= 0 ? '+' : ''}${roll}° · HDG ${hdg}°`
    }
    return activeThemeMeta.value.eyebrow
})

// F1 双面穿界星盘需要精确对齐右侧登录区宽度（lg:420px / xl:460px）
const panelWrapRef = ref<HTMLElement | null>(null)
const panelPixelWidth = ref(460)
let panelResizeObserver: ResizeObserver | null = null

const measurePanelWidth = () => {
    const el = panelWrapRef.value
    if (el) {
        const w = Math.round(el.getBoundingClientRect().width)
        if (w > 0) {
            panelPixelWidth.value = w
            return
        }
    }
    panelPixelWidth.value = window.innerWidth >= 1280 ? 460 : 420
}

watch(activeThemeId, () => {
    void nextTick(measurePanelWidth)
})

const isFloatingPanel = computed(() => {
    const v = activeThemeMeta.value.panelVariant
    return v === 'optical-bezel' || v === 'titanium-card' || v === 'daylight-card' || v === 'lab-card' || v === 'hud-bezel'
})

const isBezelPanel = computed(() => {
    const v = activeThemeMeta.value.panelVariant
    return v === 'optical-bezel' || v === 'hud-bezel'
})

// ---------------------------------------------------------------------------
// 认证表单核心状态（保持与既有业务契约完全一致）
// ---------------------------------------------------------------------------
const activeTab = ref<'sso' | 'password' | 'apikey'>('password')
const ssoEnabled = ref(false)
const apiKey = ref('')
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

// SSO 登录方式：适配 portal-web 的 check/login 必传 loginType。
// 默认值由 /auth/config/public 的 sso_login_type 给出（即后台 SSO_LOGIN_DEFAULT_TYPE 配置），
// 用户可以在登录页临时切到另一种；两种凭据不通用，所以要显式让用户选而不是猜。
const SSO_LOGIN_TYPE_OPTIONS = [
    { value: 'ad', label: '域账号' },
    { value: 'local', label: '本地密码' },
] as const
type SsoLoginType = (typeof SSO_LOGIN_TYPE_OPTIONS)[number]['value']
const ssoLoginType = ref<SsoLoginType>('ad')

// 后台开关（SSO_LOGIN_AD / SSO_LOGIN_LOCAL）允许的方式。默认「都可用」：
// 配置接口抖动时不能反过来把登录整条锁死——真正的把关在后端，它会拒绝被关闭的方式。
const ssoEnabledTypes = ref<string[]>(SSO_LOGIN_TYPE_OPTIONS.map((o) => o.value))
const isSsoTypeEnabled = (value: string) => ssoEnabledTypes.value.includes(value)
// 两种方式都被关掉 = 这个部署不提供 SSO 登录：输入框和提交按钮整体禁用
const ssoAvailable = computed(() => ssoEnabledTypes.value.length > 0)

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
    resetNameCheck()
})

const isTwoFactorStep = ref(false)
const twoFactorToken = ref('')
const twoFactorUsername = ref('')
const twoFactorCode = ref('')

const handleLoginSuccess = (userData: any) => {
    // 上一个身份可能留下了侧栏快照（同一浏览器直接换账号登录，或上次没走登出）：
    // 先清掉，否则新用户首屏会先闪出别人的菜单，再被后台校验结果替换。
    clearNavigationSnapshot()
    persistUserInfo(userData)
    // 凭据由后端下发的 HttpOnly Cookie 承载，不再写入 localStorage：
    // 落在 localStorage 的只是 JS 可读的副本，任何一次 XSS 都能带走它。

    // 落地页按界面模式分：用户模式直接落在「新建任务」（可以立刻开聊），
    // 专业模式保留原口径——普通用户工作台、管理员平台概览。
    //
    // 这里只能同步判断：登录当下还没拉 `/portal-prefs`，等接口回来再决定落地页会先
    // 跳一次再跳一次。所以先看本机缓存里这个人上次选的模式，没有则按角色给默认，
    // 真正的权威值随后由 Dashboard 的 `ensureUiMode()` 覆盖。
    const returnPath = typeof route.query.next === 'string'
      && route.query.next.startsWith('/')
      && !route.query.next.startsWith('//')
      ? route.query.next
      : ''
    const uiMode = resolveInitialUiMode(userData?.role)
    // 两种模式各自的默认主题：用户模式亮色、专业模式深色
    if (uiMode === 'user') applyUserModeDefaultTheme()
    else applyProModeDefaultTheme()
    if (returnPath) {
      // OAuth 授权端点由后端处理；必须整页请求，不能只让 SPA 改地址。
      if (returnPath.startsWith('/oauth/authorize')) {
        window.location.assign(returnPath)
      } else {
        router.push(returnPath)
      }
    } else if (uiMode === 'user') {
      // new=1：挂件据此跳过会话续接，开一条空白会话（否则会接回上次对话）
      router.push({ path: '/dashboard/chat', query: { new: '1' } })
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

/**
 * 是否隐藏「API Key」登录 Tab。
 *
 * 取值来自公开配置 `hide_login_apikey`，只能等 `/api/portal/auth/config/public` 返回才知道。
 * 初值必须是「隐藏」：按「显示」渲染会在首帧多出一个 Tab，配置回来后再删掉，
 * 肉眼就是「API Key 闪一下然后消失」。默认隐藏则隐藏环境从不出现，允许 API Key 的环境
 * 是 Tab 稍后出现（不刺眼），配置没拿到时由 fetchPublicConfig 的 finally 回退为显示。
 */
const hideLoginApiKey = ref(true)

// ---------------------------------------------------------------------------
// 账号自主注册申请
// ---------------------------------------------------------------------------
const registrationEnabled = ref(false)
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

const USER_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{2,31}$/
const USER_NAME_FORMAT_HINT = '账号名需为 3–32 位、以字母开头，仅可包含字母、数字、下划线、中划线与点'
const PASSWORD_RULE_HINT = '8–32 位，须包含大写字母、小写字母、数字、特殊符号中的至少 3 种，且不能包含账号名'
const REAL_NAME_MAX_LENGTH = 50
const REMARK_MAX_LENGTH = 255

const EMAIL_MAX_LENGTH = 254
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const registerEmailFormatError = computed(() => {
    const email = registerForm.email.trim()
    if (!email) return ''
    if (email.length > EMAIL_MAX_LENGTH || !EMAIL_PATTERN.test(email)) return '邮箱格式不正确，请检查后重试'
    return ''
})

type NameCheckState = 'idle' | 'checking' | 'available' | 'taken' | 'invalid' | 'error'
const nameCheckState = ref<NameCheckState>('idle')
const nameCheckMessage = ref('')
const nameCheckDisabled = ref(false)
const NAME_CHECK_DEBOUNCE_MS = 450
let nameCheckTimer: ReturnType<typeof setTimeout> | null = null
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
            nameCheckDisabled.value = true
            nameCheckState.value = 'idle'
            nameCheckMessage.value = ''
            return
        }
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
    if (!USER_NAME_PATTERN.test(name)) {
        nameCheckSeq++
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
        if (message.includes('已被占用')) {
            registerUserNameError.value = message
            nameCheckState.value = 'taken'
            nameCheckMessage.value = message
        } else {
            registerError.value = message
        }
    } finally {
        registerLoading.value = false
    }
}

const panelTitle = computed(() => {
    if (isTwoFactorStep.value) return '两步验证'
    if (isRegisterStep.value) return registerSuccess.value ? '申请已提交' : '申请账号'
    return '登录工作台'
})
const panelSubtitle = computed(() => {
    if (isTwoFactorStep.value) return '该账号已启用两步验证保护，请输入动态验证码'
    if (isRegisterStep.value) {
        return registerSuccess.value
            ? '管理员审核通过后即可使用该账号登录'
            : '填写信息提交注册申请，需管理员审核通过后才能登录'
    }
    return '选择认证方式以进入智能体平台'
})

const fetchPublicConfig = async () => {
    let configLoaded = false
    try {
        const response = await axios.get('/api/portal/auth/config/public')
        if (response.data?.status === 'success') {
            configLoaded = true
            ssoEnabled.value = response.data.data?.yovole_sso_enabled === true
            const enabledSsoTypes = response.data.data?.sso_login_enabled_types
            if (Array.isArray(enabledSsoTypes)) {
                ssoEnabledTypes.value = enabledSsoTypes.filter(
                    (t: string) => t === 'ad' || t === 'local'
                )
            }
            const configuredSsoLoginType = response.data.data?.sso_login_type
            if (
                (configuredSsoLoginType === 'ad' || configuredSsoLoginType === 'local') &&
                isSsoTypeEnabled(configuredSsoLoginType)
            ) {
                ssoLoginType.value = configuredSsoLoginType
            } else if (ssoEnabledTypes.value.length > 0) {
                ssoLoginType.value = ssoEnabledTypes.value[0] as SsoLoginType
            }
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
    } finally {
        if (!configLoaded) hideLoginApiKey.value = false
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
    measurePanelWidth()
    window.addEventListener('resize', measurePanelWidth)
    if (typeof ResizeObserver !== 'undefined' && panelWrapRef.value) {
        panelResizeObserver = new ResizeObserver(measurePanelWidth)
        panelResizeObserver.observe(panelWrapRef.value)
    }
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
    window.removeEventListener('resize', measurePanelWidth)
    panelResizeObserver?.disconnect()
    panelResizeObserver = null
    resetNameCheck()
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
        if (!ssoAvailable.value) { error.value = 'SSO 登录已关闭，请联系管理员'; return }
        if (!isSsoTypeEnabled(ssoLoginType.value)) {
            error.value = '该登录方式已被管理员关闭，请换一种方式'
            return
        }
        if (!username.value || !password.value) { error.value = '请完善 SSO 账号信息'; return }
        payload = {
            username: username.value,
            password: password.value,
            login_type: ssoLoginType.value,
        }
        endpoint = '/api/portal/auth/sso/login'
    } else return

    loading.value = true
    error.value = ''
    try {
        const response = await axios.post(endpoint, payload)
        if (response.data?.status === 'two_factor_required') {
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
  <div
    class="login-shell relative w-screen flex font-sans overflow-hidden"
    :class="[`login-theme-${activeThemeId}`, activeThemeMeta.isLightStage ? 'is-light-stage' : 'is-dark-stage']"
    :data-login-theme="activeThemeId"
  >
    <!-- 8 套主题统一 Canvas 视觉引擎 -->
    <LoginThemeBackdrop
      :theme="activeThemeId"
      :active-satellite="activeSatelliteIndex"
      :panel-width="panelPixelWidth"
      @pointer-change="handleBackdropPointer"
    />

    <!-- 个人中心跳转全屏预览时显示的悬浮工具条（正常访问登录页不渲染） -->
    <div
      v-if="isPreviewMode"
      class="preview-bar fixed top-3 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full bg-slate-950/85 border border-white/15 px-3 py-1.5 text-xs text-slate-100 shadow-2xl backdrop-blur-xl max-w-[calc(100vw-24px)]"
    >
      <span class="hidden sm:inline font-mono text-[11px] text-sky-300 shrink-0">预览模式</span>
      <div class="flex items-center gap-1 overflow-x-auto scrollbar-none">
        <button
          v-for="item in LOGIN_THEMES"
          :key="item.id"
          type="button"
          class="rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors whitespace-nowrap cursor-pointer"
          :class="activeThemeId === item.id ? 'bg-white text-slate-950 font-semibold' : 'text-slate-300 hover:bg-white/10 hover:text-white'"
          @click="selectPreviewTheme(item.id)"
        >
          {{ item.code }}
        </button>
      </div>
      <span class="h-3.5 w-px bg-white/15 shrink-0" aria-hidden="true" />
      <button
        type="button"
        :disabled="applyingPreview || loginTheme === activeThemeId"
        class="rounded-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1 text-[11px] font-semibold text-white transition-colors whitespace-nowrap cursor-pointer shrink-0"
        @click="applyPreviewTheme"
      >
        {{ loginTheme === activeThemeId ? '已为当前风格' : (applyingPreview ? '保存中...' : '应用此风格') }}
      </button>
      <button
        type="button"
        class="rounded-full bg-white/10 hover:bg-white/20 px-2.5 py-1 text-[11px] text-slate-200 transition-colors whitespace-nowrap cursor-pointer shrink-0"
        @click="exitPreviewMode"
      >
        返回
      </button>
      <span
        v-if="previewNotice"
        class="text-[11px] whitespace-nowrap"
        :class="
          previewNoticeTone === 'error'
            ? 'text-rose-300'
            : previewNoticeTone === 'warning'
              ? 'text-amber-300'
              : 'text-emerald-300'
        "
      >{{ previewNotice }}</span>
    </div>

    <!-- 方案 G 专属竖排建筑落款 -->
    <span
      v-if="activeThemeId === 'g'"
      aria-hidden="true"
      class="hidden lg:block pointer-events-none select-none absolute left-6 top-1/2 -translate-y-1/2 z-10 font-mono text-[10px] tracking-[0.28em] text-slate-900/30 [writing-mode:vertical-rl]"
    >
      NANZI · INTELLIGENT ARCHITECTURE
    </span>

    <!-- 主内容布局层 -->
    <div
      class="relative z-10 flex w-full h-full min-h-0"
      :class="[
        isFloatingPanel ? 'lg:px-10 xl:px-12 lg:py-9 lg:gap-10 xl:gap-12' : '',
        isFloatingPanel && isPreviewMode ? 'lg:pt-16' : '',
      ]"
    >
      <!-- Left Section: Theme Visual Stage -->
      <div
        class="hidden lg:flex flex-1 min-w-0 relative flex-col justify-between pointer-events-none"
        :class="[
          isFloatingPanel ? 'py-2' : 'px-10 xl:px-14 py-10 xl:py-12',
          !isFloatingPanel && isPreviewMode ? 'lg:pt-16' : '',
        ]"
      >
        <!-- Brand Lockup -->
        <div
          class="flex items-center gap-3 pointer-events-auto"
          :class="[
            activeThemeMeta.isLightStage ? 'text-slate-900' : 'text-white',
            activeThemeId === 'g' ? 'pl-4' : '',
          ]"
        >
          <img
            :src="iconUrl"
            class="w-10 h-10 rounded-xl object-cover shadow-lg ring-1 ring-white/15 shrink-0"
            alt="NanZi logo"
          />
          <div class="text-left min-w-0">
            <div class="text-base font-bold tracking-tight truncate">{{ productName }}</div>
            <div
              class="text-[10px] font-mono tracking-[0.14em] uppercase truncate"
              :class="activeThemeMeta.isLightStage ? 'text-slate-500' : 'text-slate-300/80'"
            >
              {{ loginSubtitle }}
            </div>
          </div>
        </div>

        <!-- Center Copy (per theme aesthetic) -->
        <div
          class="max-w-[520px]"
          :class="[
            activeThemeId === 'g' ? 'pl-4' : '',
            activeThemeId === 'h' ? 'rounded-2xl bg-[#f1f4f8]/75 backdrop-blur-md border border-slate-900/8 p-6' : '',
          ]"
        >
          <div
            class="inline-flex items-center gap-2 font-mono text-[11px] tracking-[0.11em] uppercase mb-4"
            :class="{
              'text-sky-300': activeThemeId === 'f1' || activeThemeId === 'f2' || activeThemeId === 'f3' || activeThemeId === 'i',
              'text-blue-600 font-semibold': activeThemeId === 'f4' || activeThemeId === 'h',
              'text-slate-500': activeThemeId === 'g',
              'text-emerald-400': activeThemeId === 'j',
            }"
          >
            <span
              v-if="activeThemeId === 'f1' || activeThemeId === 'f2' || activeThemeId === 'f3'"
              class="h-1.5 w-1.5 rounded-full bg-sky-400 shadow-[0_0_8px_#38bdf8] animate-pulse"
              aria-hidden="true"
            />
            <span>{{ liveTelemetryText }}</span>
          </div>

          <h1
            class="text-4xl xl:text-5xl font-semibold leading-[1.15] tracking-tight mb-4"
            :class="activeThemeMeta.isLightStage ? 'text-slate-900' : 'text-white'"
          >
            {{ activeThemeMeta.headlineLines[0] }}<br />
            {{ activeThemeMeta.headlineLines[1] }}
          </h1>

          <p
            v-if="activeThemeId !== 'f3'"
            class="text-sm xl:text-[15px] leading-relaxed max-w-[460px]"
            :class="
              activeThemeId === 'i'
                ? 'text-slate-100/85'
                : activeThemeId === 'j'
                  ? 'text-[#8da39a]'
                  : activeThemeMeta.isLightStage
                    ? 'text-slate-600'
                    : 'text-slate-400'
            "
          >
            {{ activeThemeMeta.description }}
          </p>
        </div>

        <!-- F3 专属：底部 4 颗能力卫星互动切换卡 -->
        <div
          v-if="activeThemeId === 'f3'"
          class="pointer-events-auto max-w-[490px] rounded-2xl bg-[#0d1524]/75 border border-sky-300/20 p-4 backdrop-blur-md shadow-2xl"
        >
          <div class="flex flex-wrap gap-1.5 mb-3">
            <button
              v-for="(sat, idx) in F3_SATELLITES"
              :key="sat.name"
              type="button"
              class="rounded-full border px-2.5 py-1 font-mono text-[11px] transition-all cursor-pointer"
              :class="
                activeSatelliteIndex === idx
                  ? 'border-sky-400 bg-sky-500/20 text-sky-100'
                  : 'border-slate-400/20 bg-white/5 text-slate-400 hover:border-sky-300/45 hover:text-slate-200'
              "
              @click="activeSatelliteIndex = idx"
            >
              {{ sat.label }}
            </button>
          </div>
          <div class="text-sm font-semibold text-white mb-1">{{ activeSatellite.title }}</div>
          <p class="text-xs leading-relaxed text-slate-400">{{ activeSatellite.desc }}</p>
        </div>

        <!-- 其余主题底部极简工程角标 -->
        <div
          v-else
          class="flex items-center gap-4 font-mono text-[11px]"
          :class="[
            activeThemeId === 'g' ? 'pl-4 text-slate-500' : '',
            activeThemeId === 'h' ? 'w-fit rounded-full bg-white/75 border border-slate-900/8 px-3.5 py-1.5 text-slate-600' : '',
            activeThemeId === 'i' ? 'text-slate-200/70' : '',
            activeThemeId === 'j' ? 'text-[#5b736a]' : '',
            activeThemeId === 'f1' || activeThemeId === 'f2' ? 'text-slate-500' : '',
            activeThemeId === 'f4' ? 'text-slate-500' : '',
          ]"
        >
          <template v-for="(footItem, fIdx) in activeThemeMeta.footerItems" :key="footItem">
            <span>{{ footItem }}</span>
            <span v-if="fIdx < activeThemeMeta.footerItems.length - 1" aria-hidden="true">·</span>
          </template>
        </div>
      </div>

      <!-- 方案 I 专属：长虹压花玻璃与雾白侧舱之间的 52px 羽化过渡带 -->
      <div
        v-if="activeThemeId === 'i'"
        aria-hidden="true"
        class="hidden lg:block w-[52px] h-full shrink-0 fluted-transition-slats"
      />

      <!-- Right Section: Login Panel Container -->
      <div
        ref="panelWrapRef"
        class="w-full lg:w-[420px] xl:w-[460px] h-full min-h-0 flex shrink-0 z-20"
        :class="isFloatingPanel ? 'lg:items-center' : ''"
      >
        <div
          class="w-full min-h-0 flex"
          :class="[
            isBezelPanel ? `panel-bezel panel-bezel-${activeThemeMeta.panelVariant}` : '',
            isFloatingPanel ? 'h-full lg:h-auto lg:max-h-full' : 'h-full',
          ]"
        >
          <!-- min-h-0 + overflow-y-auto：移动端注册表单高于视口时必须在面板内部滚动 -->
          <div
            class="login-panel-surface w-full min-h-0 overflow-y-auto flex flex-col bg-white relative"
            :class="[`surface-${activeThemeMeta.panelVariant}`, isFloatingPanel ? 'max-h-full' : '']"
          >
            <!-- Mobile Header (Visible only on small screens) -->
            <div class="lg:hidden pt-8 px-6 pb-0 animate-fade-in shrink-0" :class="{ 'mt-10': isPreviewMode }">
              <div class="flex items-center gap-3 mb-2">
                <img :src="iconUrl" class="w-8 h-8 rounded-lg drop-shadow-md object-cover" alt="Logo" />
                <h1 class="text-xl font-bold text-slate-900 tracking-tight">{{ productName }}</h1>
              </div>
              <p class="text-xs text-slate-500 tracking-wide uppercase">{{ loginSubtitle }}</p>
            </div>

            <!-- 移动端从顶部排布（lg 起才垂直居中）；注册表单较高时强制顶部对齐防裁切，悬浮卡在 xl 下保留上下呼吸留白 -->
            <div
              class="flex-1 flex flex-col px-6 lg:px-7 xl:px-10 py-6 xl:py-0 lg:justify-center"
              :class="[
                isRegisterStep ? '!justify-start !py-6' : (isFloatingPanel ? 'xl:py-8' : ''),
              ]"
            >
              <div class="mb-6 xl:mb-8">
                <div class="flex items-center justify-between font-mono text-[10px] tracking-[0.12em] uppercase text-slate-500 mb-2">
                  <span>PORTAL ACCESS</span>
                  <span
                    class="inline-flex items-center gap-1.5"
                    :class="activeThemeId === 'j' ? 'text-emerald-600' : 'text-sky-600'"
                  >
                    <span
                      class="h-1.5 w-1.5 rounded-full"
                      :class="activeThemeId === 'j' ? 'bg-emerald-500' : 'bg-sky-500'"
                      aria-hidden="true"
                    />
                    READY
                  </span>
                </div>
                <h2 class="text-2xl font-bold text-slate-900 tracking-tight">
                  {{ panelTitle }}
                </h2>
                <p class="text-slate-500 text-xs mt-1.5">
                  {{ panelSubtitle }}
                </p>
              </div>

              <!-- 常规登录 Tabs (仅在非 2FA、非注册阶段展示) -->
              <div
                v-if="!isTwoFactorStep && !isRegisterStep"
                class="flex gap-1 rounded-xl bg-slate-900/[0.055] border border-slate-900/[0.07] p-1 mb-5 xl:mb-6"
              >
                <button
                  v-for="tab in [{id:'sso', name:'SSO 登录'}, {id:'password', name:'本地账号'}, {id:'apikey', name:'API Key'}].filter(t => (t.id !== 'sso' || (ssoEnabled && showSsoFromBranding)) && (t.id !== 'apikey' || !hideLoginApiKey))"
                  :key="tab.id"
                  type="button"
                  @click="activeTab = tab.id as any"
                  class="flex-1 rounded-lg py-2 px-2.5 text-xs sm:text-[13px] font-medium transition-all cursor-pointer"
                  :class="activeTab === tab.id ? 'bg-white/95 text-slate-950 font-semibold shadow-sm' : 'text-slate-500 hover:text-slate-800'"
                >
                  {{ tab.name }}
                </button>
              </div>

              <!-- 常规登录表单 -->
              <form v-if="!isTwoFactorStep && !isRegisterStep" @submit.prevent="handleLogin" class="space-y-4 xl:space-y-5">
                <div class="space-y-4">
                  <div v-if="activeTab === 'password' || activeTab === 'sso'" class="space-y-4 animate-fade-slide-up">
                    <!-- SSO 凭据方式切换 -->
                    <div v-if="activeTab === 'sso'" class="space-y-1.5">
                      <label class="text-xs font-semibold text-slate-700 ml-0.5">登录方式</label>
                      <div class="grid grid-cols-2 gap-1 rounded-lg bg-slate-900/[0.055] p-1">
                        <button
                          v-for="opt in SSO_LOGIN_TYPE_OPTIONS"
                          :key="opt.value"
                          type="button"
                          :disabled="!isSsoTypeEnabled(opt.value)"
                          @click="isSsoTypeEnabled(opt.value) && (ssoLoginType = opt.value)"
                          class="relative rounded-md px-3 py-1.5 text-xs font-semibold transition-all"
                          :class="[
                            !isSsoTypeEnabled(opt.value)
                              ? 'cursor-not-allowed text-slate-400/70'
                              : (ssoLoginType === opt.value ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700')
                          ]"
                        >
                          <span :class="{ 'line-through decoration-slate-400': !isSsoTypeEnabled(opt.value) }">{{ opt.label }}</span>
                          <span
                            v-if="!isSsoTypeEnabled(opt.value)"
                            class="ml-1 align-middle text-[9px] font-normal text-slate-400"
                          >禁用</span>
                        </button>
                      </div>
                    </div>

                    <!-- SSO 全部禁用提示 -->
                    <div
                      v-if="activeTab === 'sso' && !ssoAvailable"
                      class="p-3 bg-amber-50/90 border border-amber-200/80 text-amber-800 text-[11px] rounded-lg flex items-center gap-2"
                    >
                      <svg class="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>
                      <span>SSO 登录已关闭（域账号与本地密码均不可用），请联系管理员，或改用其他登录方式。</span>
                    </div>

                    <div class="space-y-1.5">
                      <label class="text-xs font-semibold text-slate-700 ml-0.5">
                        {{ activeTab === 'sso' ? 'SSO 用户名' : '本地账号用户名' }}
                      </label>
                      <input
                        v-model="username"
                        type="text"
                        class="login-field-input w-full bg-white/85 border border-slate-900/15 rounded-xl px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100"
                        :placeholder="activeTab === 'sso' ? '请输入 YES 账号' : '请输入本地账号用户名'"
                        :disabled="activeTab === 'sso' && !ssoAvailable"
                      />
                    </div>

                    <div class="space-y-1.5">
                      <label class="text-xs font-semibold text-slate-700 ml-0.5">密码</label>
                      <input
                        v-model="password"
                        type="password"
                        class="login-field-input w-full bg-white/85 border border-slate-900/15 rounded-xl px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100"
                        placeholder="请输入密码"
                        :disabled="activeTab === 'sso' && !ssoAvailable"
                      />
                    </div>

                    <div v-if="activeTab === 'sso'" class="flex items-center gap-1.5 ml-0.5 animate-fade-in">
                      <div class="w-1.5 h-1.5 rounded-full bg-sky-500/80"></div>
                      <span class="text-[11px] text-slate-500">
                        {{ ssoLoginType === 'ad' ? '提示：请使用域账号（YES 账号）密码登录' : '提示：请使用门户设置的本地密码登录' }}
                      </span>
                    </div>
                  </div>

                  <div v-if="activeTab === 'apikey'" class="animate-fade-slide-up">
                    <div class="space-y-1.5">
                      <label class="text-xs font-semibold text-slate-700 ml-0.5">API Key (X-API-Key)</label>
                      <textarea
                        v-model="apiKey"
                        rows="3"
                        class="login-field-input w-full bg-white/85 border border-slate-900/15 rounded-xl px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:bg-white transition-all resize-none font-mono disabled:bg-gray-100"
                        placeholder="ys_..."
                      ></textarea>
                    </div>
                  </div>
                </div>

                <div v-if="error" class="p-3 bg-red-50/90 border border-red-200/80 text-red-700 text-xs rounded-xl flex items-center gap-2 animate-shake">
                  <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                  <span>认证失败: {{ error }}</span>
                </div>

                <button
                  type="submit"
                  class="login-submit-btn w-full text-white rounded-xl py-3 text-sm font-semibold transition-all active:scale-[0.985] disabled:opacity-65 disabled:cursor-not-allowed flex justify-center items-center gap-2 cursor-pointer"
                  :class="`submit-${activeThemeMeta.submitVariant}`"
                  :disabled="loading || (activeTab === 'sso' && !ssoAvailable)"
                >
                  <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-1"></span>
                  <span>{{ loading ? '连接中...' : (activeTab === 'sso' ? '统一认证登录' : activeTab === 'apikey' ? '使用密钥进入' : '进入工作台') }}</span>
                  <svg v-if="!loading" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14m-6-6l6 6-6 6" />
                  </svg>
                </button>

                <!-- 底部辅助入口：注册申请与忘记密码。
                     两者各自独立受配置控制（user_registration_enabled / password_reset_available），
                     可能只出现其中一个；因此用「居中 + 细分隔符」而不是左右分列。 -->
                <div
                  v-if="activeTab === 'password' && (registrationEnabled || passwordResetAvailable)"
                  class="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 pt-1 text-xs"
                >
                  <span v-if="registrationEnabled" class="flex items-center">
                    <span class="text-slate-500">还没有账号？</span>
                    <button
                      type="button"
                      @click="openRegister"
                      class="text-xs font-semibold text-blue-600 hover:text-blue-700 transition-colors ml-1 cursor-pointer"
                    >
                      申请账号
                    </button>
                  </span>

                  <span
                    v-if="registrationEnabled && passwordResetAvailable"
                    aria-hidden="true"
                    class="h-3 w-px bg-slate-300"
                  ></span>

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
                <div class="p-3.5 bg-white/70 rounded-xl border border-slate-900/10 flex items-center gap-3">
                  <div class="w-9 h-9 rounded-lg bg-slate-900 text-sky-300 flex items-center justify-center flex-shrink-0 shadow-sm">
                    <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M12 3l7 4v5c0 5-3.5 8.5-7 9-3.5-.5-7-4-7-9V7l7-4z" />
                      <path stroke-linecap="round" stroke-linejoin="round" d="M9.5 12.5l1.8 1.8 3.7-4" />
                    </svg>
                  </div>
                  <div class="min-w-0">
                    <div class="flex items-center gap-2">
                      <span class="text-xs font-bold text-slate-800">Google 身份验证器</span>
                      <span class="text-[10px] px-1.5 py-0.5 bg-slate-900/10 text-slate-700 rounded font-mono">2FA</span>
                    </div>
                    <p class="text-[11px] text-slate-500 truncate mt-0.5 font-mono">账号：@{{ twoFactorUsername }}</p>
                  </div>
                </div>

                <div class="space-y-2">
                  <div class="flex items-center justify-between">
                    <label class="text-xs font-semibold text-slate-700">6 位动态验证码</label>
                    <span class="text-[11px] text-slate-500">30 秒自动刷新</span>
                  </div>
                  <input
                    v-model="twoFactorCode"
                    type="text"
                    maxlength="6"
                    autofocus
                    class="login-field-input w-full bg-white/85 border border-slate-900/15 rounded-xl py-3 text-center text-2xl font-mono tracking-[0.35em] text-slate-900 outline-none focus:bg-white transition-all font-bold placeholder:text-slate-300 placeholder:tracking-widest disabled:bg-gray-100"
                    placeholder="000000"
                  />
                </div>

                <div v-if="error" class="p-3 bg-red-50/90 border border-red-200/80 text-red-700 text-xs rounded-xl flex items-center gap-2 animate-shake">
                  <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                  <span>验证失败: {{ error }}</span>
                </div>

                <button
                  type="submit"
                  class="login-submit-btn w-full text-white rounded-xl py-3 text-sm font-semibold transition-all active:scale-[0.985] disabled:opacity-65 flex justify-center items-center cursor-pointer"
                  :class="`submit-${activeThemeMeta.submitVariant}`"
                  :disabled="loading || !twoFactorCode || twoFactorCode.length !== 6"
                >
                  <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-3"></span>
                  {{ loading ? '验证中...' : '完成验证并进入平台' }}
                </button>

                <div class="text-center pt-1">
                  <button
                    type="button"
                    @click="cancelTwoFactor"
                    class="text-xs text-slate-500 hover:text-slate-800 transition-colors inline-flex items-center gap-1 font-medium cursor-pointer"
                  >
                    <span>← 返回修改账号密码</span>
                  </button>
                </div>
              </form>

              <!-- 账号注册申请表单 -->
              <form v-else-if="!registerSuccess" @submit.prevent="handleRegister" class="space-y-4 animate-fade-slide-up">
                <div class="space-y-3.5">
                  <div class="space-y-1.5">
                    <label class="text-xs font-semibold text-slate-700 ml-0.5">账号名</label>
                    <input
                      v-model="registerForm.user_name"
                      type="text"
                      autocomplete="username"
                      autocapitalize="none"
                      autocorrect="off"
                      spellcheck="false"
                      class="w-full bg-white/85 border rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      :class="
                        registerUserNameError || nameCheckState === 'taken'
                          ? 'border-red-400 focus:border-red-500'
                          : nameCheckState === 'available'
                            ? 'border-emerald-400 focus:border-emerald-500'
                            : 'border-slate-900/15 focus:border-blue-500'
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
                    <label class="text-xs font-semibold text-slate-700 ml-0.5">用户姓名</label>
                    <input
                      v-model="registerForm.real_name"
                      type="text"
                      :maxlength="REAL_NAME_MAX_LENGTH"
                      class="w-full bg-white/85 border border-slate-900/15 rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      placeholder="请输入真实姓名"
                    />
                  </div>

                  <div class="space-y-1.5">
                    <label class="text-xs font-semibold text-slate-700 ml-0.5">邮箱（选填，用于接收审核结果通知）</label>
                    <input
                      v-model="registerForm.email"
                      type="email"
                      autocomplete="email"
                      autocapitalize="none"
                      autocorrect="off"
                      spellcheck="false"
                      :maxlength="EMAIL_MAX_LENGTH"
                      class="w-full bg-white/85 border rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      :class="registerEmailFormatError ? 'border-red-400 focus:border-red-500' : 'border-slate-900/15 focus:border-blue-500'"
                      placeholder="name@example.com"
                    />
                    <p v-if="registerEmailFormatError" class="text-[11px] text-red-600 ml-1">{{ registerEmailFormatError }}</p>
                    <p v-else class="text-[10px] text-slate-500 ml-1 leading-relaxed">审核结果将通过邮件发送到该地址；不填写则仅站内查看</p>
                  </div>

                  <div class="space-y-1.5">
                    <label class="text-xs font-semibold text-slate-700 ml-0.5">密码</label>
                    <input
                      v-model="registerForm.password"
                      type="password"
                      autocomplete="new-password"
                      class="w-full bg-white/85 border border-slate-900/15 rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      placeholder="••••••••"
                    />
                    <p class="text-[10px] text-slate-500 ml-1 leading-relaxed">{{ PASSWORD_RULE_HINT }}</p>
                  </div>

                  <div class="space-y-1.5">
                    <label class="text-xs font-semibold text-slate-700 ml-0.5">确认密码</label>
                    <input
                      v-model="registerForm.confirmPassword"
                      type="password"
                      autocomplete="new-password"
                      class="w-full bg-white/85 border border-slate-900/15 rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      placeholder="请再次输入密码"
                    />
                  </div>

                  <div class="space-y-1.5">
                    <div class="flex items-center justify-between ml-0.5">
                      <label class="text-xs font-semibold text-slate-700">备注（可选）</label>
                      <span class="text-[10px] text-slate-400 font-mono">{{ registerForm.remark.length }}/{{ REMARK_MAX_LENGTH }}</span>
                    </div>
                    <textarea
                      v-model="registerForm.remark"
                      rows="2"
                      :maxlength="REMARK_MAX_LENGTH"
                      class="w-full bg-white/85 border border-slate-900/15 rounded-xl px-4 py-3 text-base text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all resize-none disabled:bg-gray-100 sm:py-2.5 sm:text-sm"
                      placeholder="可填写所属部门、申请用途等，便于管理员审核"
                    ></textarea>
                  </div>
                </div>

                <div v-if="registerError" class="p-3 bg-red-50/90 border border-red-200/80 text-red-700 text-xs rounded-xl flex items-center gap-2 animate-shake">
                  <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                  <span>{{ registerError }}</span>
                </div>

                <button
                  type="submit"
                  class="login-submit-btn w-full text-white rounded-xl py-3 text-sm font-semibold transition-all active:scale-[0.985] disabled:opacity-65 flex justify-center items-center cursor-pointer"
                  :class="`submit-${activeThemeMeta.submitVariant}`"
                  :disabled="registerLoading"
                >
                  <span v-if="registerLoading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-3"></span>
                  {{ registerLoading ? '提交中...' : '提交注册申请' }}
                </button>

                <div class="text-center pt-1">
                  <button
                    type="button"
                    @click="backToLogin"
                    class="text-xs text-slate-500 hover:text-slate-800 transition-colors inline-flex items-center gap-1 font-medium cursor-pointer"
                  >
                    <span>← 返回登录</span>
                  </button>
                </div>
              </form>

              <!-- 注册申请提交成功 -->
              <div v-else class="space-y-5 animate-fade-slide-up text-center">
                <div class="mx-auto w-14 h-14 rounded-2xl bg-emerald-50 border border-emerald-200/70 flex items-center justify-center">
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
                  class="login-submit-btn w-full text-white rounded-xl py-3 text-sm font-semibold transition-all active:scale-[0.985] cursor-pointer"
                  :class="`submit-${activeThemeMeta.submitVariant}`"
                >
                  返回登录
                </button>
              </div>
            </div>

            <!-- 底部版权 / 安全状态 -->
            <div v-if="showCopyright" class="px-6 py-4 border-t border-slate-900/[0.07] text-center shrink-0">
              <p class="login-copyright text-[10px] text-slate-500 font-light tracking-[0.18em] leading-[1.7] whitespace-pre-line">
                {{ copyrightText }}
              </p>
            </div>
            <div v-else class="px-6 py-4 border-t border-slate-900/[0.07] flex items-center justify-between text-[10px] text-slate-400 font-mono shrink-0">
              <span>© 2026 NanZi Platform</span>
              <span>TLS · HttpOnly Session</span>
            </div>
          </div>
        </div>
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

/* 8 套主题底色兜底（Canvas 首帧渲染前零白屏闪烁） */
.login-theme-f1 { background: #070d18; }
.login-theme-f2 { background: #070b14; }
.login-theme-f3 { background: #080d17; }
.login-theme-f4 { background: #edf1f7; }
.login-theme-g { background: #f5f3ee; }
.login-theme-h { background: #f1f4f8; }
.login-theme-i { background: #0c1629; }
.login-theme-j { background: #0b110f; }

/* 双层光学外鞘（F2 冰雾外鞘 & J 翡翠航电外鞘） */
@media (min-width: 1024px) {
  .panel-bezel {
    padding: 10px;
    border-radius: 28px;
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
  }
  .panel-bezel-optical-bezel {
    background: linear-gradient(155deg, rgba(148, 163, 184, 0.22) 0%, rgba(30, 41, 59, 0.28) 55%, rgba(56, 189, 248, 0.16) 100%);
    border: 1px solid rgba(186, 230, 253, 0.22);
    box-shadow: 0 34px 90px rgba(0, 0, 0, 0.56), 0 0 64px rgba(37, 99, 235, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.18);
  }
  .panel-bezel-hud-bezel {
    background: linear-gradient(155deg, rgba(167, 243, 208, 0.16) 0%, rgba(20, 34, 29, 0.45) 60%, rgba(16, 185, 129, 0.14) 100%);
    border: 1px solid rgba(110, 231, 183, 0.22);
    box-shadow: 0 32px 86px rgba(0, 0, 0, 0.55);
  }
}

/* 右侧登录区按主题微调：消除深景亮卡突兀感 */
.login-panel-surface.surface-split-mist {
  background: rgba(242, 246, 252, 0.96);
}
@media (min-width: 1024px) {
  /* F1 桌面端透明叠在 Canvas 亮半区上，让穿界旋转的星盘细线直接透出 */
  .login-panel-surface.surface-split-mist {
    background: transparent;
    border-left: 1px solid rgba(148, 163, 184, 0.22);
    box-shadow: -28px 0 70px rgba(7, 13, 24, 0.32);
  }
  .login-panel-surface.surface-optical-bezel {
    border-radius: 19px;
    background: linear-gradient(180deg, rgba(242, 246, 252, 0.94) 0%, rgba(230, 238, 248, 0.92) 100%);
    border: 1px solid rgba(255, 255, 255, 0.85);
  }
  .login-panel-surface.surface-titanium-card {
    border-radius: 22px;
    background: linear-gradient(165deg, #e7edf5 0%, #dce4f0 100%);
    border: 1px solid rgba(226, 236, 248, 0.65);
    box-shadow: 0 28px 80px rgba(0, 0, 0, 0.55), 0 0 90px rgba(56, 189, 248, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.8);
  }
  .login-panel-surface.surface-daylight-card {
    border-radius: 22px;
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.96);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    box-shadow: 0 24px 64px rgba(15, 23, 42, 0.08), 0 0 0 1px rgba(15, 23, 42, 0.06);
  }
  .login-panel-surface.surface-ink-wing {
    background: rgba(251, 250, 247, 0.86);
    border-left: 1px solid rgba(28, 30, 34, 0.09);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
  }
  .login-panel-surface.surface-lab-card {
    border-radius: 20px;
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(15, 23, 42, 0.1);
    backdrop-filter: blur(22px);
    -webkit-backdrop-filter: blur(22px);
    box-shadow: 0 22px 60px rgba(15, 23, 42, 0.09);
    border-top: 2px solid #0f172a;
  }
  .login-panel-surface.surface-fluted-wing {
    background: rgba(244, 247, 252, 0.93);
    backdrop-filter: blur(28px);
    -webkit-backdrop-filter: blur(28px);
  }
  .login-panel-surface.surface-hud-bezel {
    border-radius: 17px;
    background: linear-gradient(180deg, #eff2ef 0%, #e5eae6 100%);
    border: 1px solid rgba(255, 255, 255, 0.78);
  }
}

.fluted-transition-slats {
  background: linear-gradient(90deg, rgba(244, 247, 252, 0) 0%, rgba(244, 247, 252, 0.45) 50%, rgba(244, 247, 252, 0.93) 100%);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-left: 1px solid rgba(255, 255, 255, 0.16);
}

/* 输入框聚焦微光与主题主按钮 */
.login-field-input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14);
}
.login-theme-j .login-field-input:focus {
  border-color: #059669;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15);
}
.login-theme-g .login-field-input:focus {
  border-color: #1e293b;
  box-shadow: 0 0 0 3px rgba(30, 41, 59, 0.1);
}

.login-submit-btn.submit-navy {
  background: linear-gradient(180deg, #162442 0%, #0d172b 100%);
  box-shadow: 0 8px 20px rgba(11, 20, 38, 0.24), inset 0 1px 0 rgba(125, 211, 252, 0.22);
}
.login-submit-btn.submit-navy:hover:not(:disabled) {
  filter: brightness(1.12);
}
.login-submit-btn.submit-blue {
  background: linear-gradient(180deg, #2563eb 0%, #1d4ed8 100%);
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.22);
}
.login-submit-btn.submit-blue:hover:not(:disabled) {
  filter: brightness(1.07);
}
.login-submit-btn.submit-ink {
  background: linear-gradient(180deg, #27292e 0%, #16181c 100%);
  box-shadow: 0 8px 20px rgba(20, 22, 26, 0.22);
}
.login-submit-btn.submit-ink:hover:not(:disabled) {
  filter: brightness(1.12);
}
.login-submit-btn.submit-hud {
  background: linear-gradient(180deg, #132a22 0%, #0c1c16 100%);
  box-shadow: 0 8px 20px rgba(12, 28, 22, 0.26), inset 0 1px 0 rgba(52, 211, 153, 0.28);
}
.login-submit-btn.submit-hud:hover:not(:disabled) {
  filter: brightness(1.14);
}

.animate-fade-slide-up { animation: fadeSlideUp 0.28s ease-out; }
@keyframes fadeSlideUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.animate-fade-in { animation: fadeIn 0.35s ease-out; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.animate-shake { animation: shake 0.4s cubic-bezier(.36,.07,.19,.97) both; }
@keyframes shake { 10%, 90% { transform: translate3d(-1px, 0, 0); } 20%, 80% { transform: translate3d(2px, 0, 0); } 30%, 50%, 70% { transform: translate3d(-2px, 0, 0); } 40%, 60% { transform: translate3d(2px, 0, 0); } }
</style>
