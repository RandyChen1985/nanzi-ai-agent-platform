<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import { useBranding } from '../composables/useBranding'

const router = useRouter()
const { branding, loadBranding } = useBranding()
const activeTab = ref<'sso' | 'password' | 'apikey'>('password')
const ssoEnabled = ref(false)
const apiKey = ref('')
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

const productName = computed(() => branding.value.product_name)
const loginSubtitle = computed(() => branding.value.login_subtitle)
const iconUrl = computed(() => branding.value.icon_url)
const showCopyright = computed(() => branding.value.enabled && !!(branding.value.copyright_text || '').trim())
const copyrightText = computed(() => (branding.value.copyright_text || '').trim())
const showSsoFromBranding = computed(() => !branding.value.hide_login_sso)

const displaySlides = computed(() => {
    const list = slides.map((slide) => ({ ...slide }))
    if (list[0]) {
        list[0].title = productName.value
        list[0].subtitle = loginSubtitle.value
    }
    return list
})

// Carousel Logic
const currentSlide = ref(0)
const slides = [
    {
        title: '南孜 · 智能体平台',
        subtitle: 'NanZi Intelligent Agent Platform',
        desc: '企业级多智能体编排 · 7x24 进化的数字大脑',
        features: ['智能编排', '安全管控', '实时分析'],
        gradient: 'from-blue-600/30 via-blue-950/60 to-[#020617]',
        accent: 'text-blue-400',
        glow: 'bg-blue-500/10',
        bg: 'bg-[#020617]'
    },
    {
        title: 'ChatBI 数据洞察',
        subtitle: 'Natural Language Data Insights',
        desc: '自然语言即刻透视业务指标 · 零门槛报表生成',
        features: ['NL2SQL', '自动绘图', '数据自愈'],
        gradient: 'from-emerald-600/30 via-emerald-950/60 to-[#061712]',
        accent: 'text-emerald-400',
        glow: 'bg-emerald-500/10',
        bg: 'bg-[#061712]'
    },
    {
        title: 'AIOps 自动化运维',
        subtitle: 'AI-Powered Operations',
        desc: '打通企业工具链 · 实现故障自愈与合规审计',
        features: ['Jira 对接', '代码审计', '全量审计'],
        gradient: 'from-violet-600/30 via-violet-950/60 to-[#0d0617]',
        accent: 'text-violet-400',
        glow: 'bg-violet-500/10',
        bg: 'bg-[#0d0617]'
    },
    {
        title: 'RAG 知识增强',
        subtitle: 'Enterprise Knowledge Base',
        desc: '海量 SOP 毫秒级检索 · 零幻觉的企业级大脑',
        features: ['语义搜索', '多源导入', '引用溯源'],
        gradient: 'from-orange-600/30 via-orange-950/60 to-[#170d06]',
        accent: 'text-orange-400',
        glow: 'bg-orange-500/10',
        bg: 'bg-[#170d06]'
    },
    {
        title: '智能体生态超市',
        subtitle: 'Agent Marketplace',
        desc: '开箱即用的专家矩阵 · 覆盖全业务场景',
        features: ['一键启用', '插件市场', '能力共建'],
        gradient: 'from-cyan-600/30 via-cyan-950/60 to-[#061217]',
        accent: 'text-cyan-400',
        glow: 'bg-cyan-500/10',
        bg: 'bg-[#061217]'
    }
]

let slideTimer: any = null
const startSlide = () => {
    slideTimer = setInterval(() => {
        currentSlide.value = (currentSlide.value + 1) % slides.length
    }, 5000)
}

// Clear form when switching tabs
watch(activeTab, () => {
    username.value = ''
    password.value = ''
    apiKey.value = ''
    error.value = ''
})

// Interaction logic
const mouseX = ref(0)
const mouseY = ref(0)
const handleMouseMove = (e: MouseEvent) => {
    mouseX.value = (e.clientX / window.innerWidth - 0.5) * 15
    mouseY.value = (e.clientY / window.innerHeight - 0.5) * 15
}

const fetchPublicConfig = async () => {
    try {
        const response = await axios.get('/api/portal/auth/config/public')
        if (response.data?.status === 'success') {
            ssoEnabled.value = response.data.data?.yovole_sso_enabled === true
            if (ssoEnabled.value && !branding.value.hide_login_sso) {
                activeTab.value = 'sso'
            }
        }
    } catch (e) {
        console.error('获取公开配置失败:', e)
    }
}

onMounted(async () => { 
    window.addEventListener('mousemove', handleMouseMove)
    startSlide()
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
    window.removeEventListener('mousemove', handleMouseMove)
    if (slideTimer) clearInterval(slideTimer)
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
          
          // Redirect based on role and device
          const isMobile = window.innerWidth < 768
          if (isMobile || userData.role !== 'admin') {
            router.push('/dashboard/chat')
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
    >
        <!-- Refined Mesh Gradients -->
        <div 
            class="absolute inset-0 bg-[radial-gradient(circle_at_30%_30%,_var(--tw-gradient-stops))] transition-all duration-1000 scale-150"
            :class="slides[currentSlide]?.gradient || ''"
        ></div>
        <div 
            class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[1000px] h-[1000px] rounded-full blur-[180px] animate-pulse transition-all duration-1000 opacity-20"
            :class="slides[currentSlide]?.glow || ''"
        ></div>
        
        <!-- Animated Tech Grid Decoration -->
        <div class="absolute inset-0 opacity-[0.03]" style="background-image: linear-gradient(#3b82f6 1px, transparent 1px), linear-gradient(90deg, #3b82f6 1px, transparent 1px); background-size: 60px 60px;"></div>

        <!-- Carousel Container -->
        <div class="relative w-full h-full flex items-center justify-center">
            <div 
                v-for="(slide, index) in displaySlides" 
                :key="index"
                class="absolute inset-0 flex flex-col items-center justify-center text-center px-20 transition-all duration-1000 ease-in-out"
                :class="[
                    currentSlide === index ? 'opacity-100 translate-x-0' : (currentSlide > index ? 'opacity-0 -translate-x-full' : 'opacity-0 translate-x-full')
                ]"
            >
                <div class="relative z-10" :style="{ transform: `translate(${mouseX}px, ${mouseY}px)` }">
                    <h1 class="text-7xl font-bold text-white tracking-tighter mb-4 drop-shadow-2xl">
                        {{ slide.title }}
                    </h1>
                    <p class="text-2xl text-slate-300 font-light tracking-[0.1em] mb-4">
                        {{ slide.subtitle }}
                    </p>
                    <p class="text-slate-500 text-sm tracking-[0.4em] uppercase mb-12 opacity-70">
                        {{ slide.desc }}
                    </p>
                    
                    <div class="flex items-center justify-center space-x-12">
                        <template v-for="(feature, fIndex) in slide.features" :key="fIndex">
                            <div class="flex flex-col items-center gap-2">
                                <span class="font-bold text-lg tracking-widest transition-colors duration-500" :class="slide.accent">{{ feature }}</span>
                                <span class="text-slate-600 text-[10px] uppercase font-mono tracking-tighter">Feature 0{{ fIndex + 1 }}</span>
                            </div>
                            <div v-if="fIndex < slide.features.length - 1" class="w-px h-8 bg-slate-800"></div>
                        </template>
                    </div>
                </div>
            </div>
        </div>

        <!-- Carousel Indicators -->
        <div class="absolute bottom-24 left-1/2 -translate-x-1/2 flex gap-3 z-30">
            <button 
                v-for="(_, index) in slides" 
                :key="index"
                @click="currentSlide = index"
                class="h-1.5 transition-all duration-500 rounded-full"
                :class="currentSlide === index ? `w-10 ${slides[index]?.accent?.replace('text-', 'bg-') || ''} shadow-[0_0_10px_rgba(255,255,255,0.3)]` : 'w-4 bg-slate-800 hover:bg-slate-700'"
            ></button>
        </div>
        
        <!-- Status Bar -->
        <div class="absolute bottom-10 left-12 flex items-center gap-4 opacity-40">
            <div class="flex items-center gap-2">
                <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                <span class="text-[10px] text-slate-400 font-mono tracking-widest uppercase">System Operational</span>
            </div>
            <span class="text-slate-700">|</span>
            <span class="text-[10px] text-slate-400 font-mono tracking-widest uppercase">Nodes: 0x081</span>
        </div>
    </div>

    <!-- Right Section: Compact Login Panel -->
    <div class="w-full lg:w-[500px] flex flex-col bg-white relative shadow-2xl z-20">
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

        <div class="flex-1 flex flex-col justify-center px-10">
            <div class="mb-10">
                <h2 class="text-2xl font-bold text-slate-900 tracking-tight">欢迎回来</h2>
                <p class="text-slate-400 text-xs mt-1">请输入您的凭据以访问控制台</p>
            </div>

            <!-- Tabs -->
            <div class="flex space-x-8 border-b border-slate-100 mb-8">
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

            <form @submit.prevent="handleLogin" class="space-y-6">
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

            <div class="mt-12 pt-8 border-t border-slate-50 text-center">
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