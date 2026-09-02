<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import axios from 'axios'
import { useBranding } from '../composables/useBranding'

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
        title: '让智能体成为组织的第二操作系统',
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
})

const fetchPublicConfig = async () => {
    try {
        const response = await axios.get('/api/portal/auth/config/public')
        if (response.data?.status === 'success') {
            ssoEnabled.value = response.data.data?.yovole_sso_enabled === true
            const tz = response.data.data?.platform_timezone
            if (tz) {
                const { setPlatformTimezone } = await import('@/utils/platformTimezone')
                setPlatformTimezone(tz)
            }
            if (ssoEnabled.value && !branding.value.hide_login_sso) {
                activeTab.value = 'sso'
            }
        }
    } catch (e) {
        console.error('获取公开配置失败:', e)
    }
}

onMounted(async () => { 
    currentSlide.value = getInitialSlide()
    motionMediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = motionMediaQuery.matches
    motionMediaQuery.addEventListener?.('change', handleMotionPreferenceChange)
    restartSlideTimer()
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
        if (response.data?.status === 'success') {
          const userData = response.data.data
          localStorage.setItem('user_info', JSON.stringify(userData))
          localStorage.setItem('api_key', userData.api_key)
          
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
  <div class="h-screen w-screen flex bg-slate-900 font-sans overflow-hidden">
    
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
                    <h1 class="text-5xl xl:text-7xl font-bold tracking-tighter mb-4 drop-shadow-2xl" :class="slide.light ? 'text-slate-900' : 'text-white'">
                        {{ slide.title }}
                    </h1>
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
    <div
        class="w-full lg:w-[420px] xl:w-[460px] flex flex-col bg-white relative shadow-2xl z-20"
        @focusin="pauseSlideTimer('form-focus')"
        @focusout="resumeSlideTimer('form-focus')"
    >
        <!-- Top accent -->
        <div class="h-1 w-full bg-blue-600"></div>

        <!-- Mobile Header (Visible only on small screens) -->
        <div class="lg:hidden pt-8 px-10 pb-0 animate-fade-in">
            <div class="flex items-center gap-3 mb-2">
                <img :src="iconUrl" class="w-8 h-8 rounded-lg drop-shadow-md object-cover" alt="Logo" />
                <h1 class="text-xl font-bold text-slate-900 tracking-tight">{{ productName }}</h1>
            </div>
            <p class="text-xs text-slate-500 tracking-wide uppercase">{{ loginSubtitle }}</p>
        </div>

        <div class="flex-1 flex flex-col justify-center px-6 lg:px-7 xl:px-10 py-6 xl:py-0">
            <div class="mb-6 xl:mb-10">
                <h2 class="text-2xl font-bold text-slate-900 tracking-tight">欢迎回来</h2>
                <p class="text-slate-400 text-xs mt-1">请输入您的凭据以访问控制台</p>
            </div>

            <!-- Tabs -->
            <div class="flex space-x-6 xl:space-x-8 border-b border-slate-100 mb-5 xl:mb-8">
                <button 
                    v-for="tab in [{id:'sso', name:'SSO 登录'}, {id:'password', name:'本地账号'}, {id:'apikey', name:'API Key'}].filter(t => t.id !== 'sso' || (ssoEnabled && showSsoFromBranding))" 
                    :key="tab.id"
                    @click="activeTab = tab.id as any"
                    class="pb-3 text-sm font-semibold transition-all relative"
                    :class="activeTab === tab.id ? 'text-blue-600' : 'text-slate-400 hover:text-slate-600'"
                >
                    {{ tab.name }}
                    <div v-if="activeTab === tab.id" class="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-full"></div>
                </button>
            </div>

            <form @submit.prevent="handleLogin" class="space-y-4 xl:space-y-6">
                <div class="space-y-4">
                    <div v-if="activeTab === 'password' || activeTab === 'sso'" class="space-y-4 animate-fade-slide-up">
                        <div class="space-y-1.5">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">
                                {{ activeTab === 'sso' ? 'SSO 用户名' : '本地账号用户名' }}
                            </label>
                            <input 
                                v-model="username" 
                                type="text" 
                                class="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all"
                                :placeholder="activeTab === 'sso' ? '请输入 YES 账号' : '请输入本地账号用户名'"
                            />
                        </div>
                        <div class="space-y-1.5">
                            <label class="text-[11px] font-bold text-slate-400 uppercase tracking-wider ml-1">密码</label>
                            <input 
                                v-model="password" 
                                type="password" 
                                class="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all"
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
                                class="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-blue-500 focus:bg-white transition-all resize-none font-mono"
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
            </form>

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
  </div>
</template>

<style scoped>
.animate-fade-slide-up { animation: fadeSlideUp 0.3s ease-out; }
@keyframes fadeSlideUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.animate-fade-in { animation: fadeIn 0.4s ease-out; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.animate-shake { animation: shake 0.4s cubic-bezier(.36,.07,.19,.97) both; }
@keyframes shake { 10%, 90% { transform: translate3d(-1px, 0, 0); } 20%, 80% { transform: translate3d(2px, 0, 0); } 30%, 50%, 70% { transform: translate3d(-2px, 0, 0); } 40%, 60% { transform: translate3d(2px, 0, 0); } }
</style>
