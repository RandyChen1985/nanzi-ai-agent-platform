<script setup lang="ts">
/**
 * 忘记密码 / 重置密码页。
 *
 * 两种模式由 URL 上有没有 token 决定：
 *  - 无 token：填邮箱发起申请（对应 POST /auth/password-reset/request）；
 *  - 有 token：设置新密码（对应 POST /auth/password-reset/confirm）。
 *
 * 页面刻意不复制服务端的防枚举文案：成功提示一律用响应里的 message。服务端对所有
 * 「不该发信」的情况（邮箱不存在、待审核、已禁用、被限流）都返回同一句文案，前端若
 * 另写一句就会随服务端修改而漂移，也可能把「被限流」渲染成用户可感知的差异。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'
import { clearUserSession } from '../utils/userSession'
import { useTypewriter } from '../composables/useTypewriter'

const route = useRoute()
const router = useRouter()

/**
 * 卡片顶部的循环文案：卡片顶到标题之间那块留白，原本是整页最"冷"的地方。
 *
 * 刻意**不写业务措辞**（"输入邮箱""链接多久有效"）：这行字的作用是让页面有呼吸，
 * 不是传达指引。真正要用户读到的约束（15 分钟有效期、只能用一次）留在标题下方那行
 * 静态说明里——绝不能挪进循环动画，否则用户正好错过那一轮就等于没提示过。
 *
 * 两句一组、共四句，打完退回再换下一句；顺序即节奏，短句在前更抓得住视线。
 */
const AMBIENT_LINES = [
  '记忆只是暂时离线，重新对齐就好。',
  '上下文被截断的地方，正是重写的开始。',
  '每一次遗忘，都是一次重新训练。',
  '权重会被重置，意图始终清晰。'
]

const { displayed: ambientText } = useTypewriter(AMBIENT_LINES)

const token = computed(() =>
  typeof route.query.token === 'string' ? route.query.token : ''
)
const hasToken = computed(() => token.value.length > 0)

/**
 * 链接预校验状态。有 token 时进页面立刻校验，让用户在动手填密码**之前**就知道
 * 链接还能不能用——否则用户会认真填完整套密码，点提交才被告知链接已过期。
 *
 * 'unavailable' 必须与 'invalid' 分开：把服务端 503 或网络错误渲染成「链接已失效」，
 * 用户会白重新申请一封邮件，而问题其实不在链接上；反过来把失效说成「稍后重试」，
 * 则让用户对着一条已经失效的死链反复重试。
 */
type VerifyState = 'idle' | 'checking' | 'valid' | 'invalid' | 'unavailable'
const verifyState = ref<VerifyState>('idle')

const checkToken = async () => {
  if (!hasToken.value) {
    verifyState.value = 'idle'
    return
  }
  verifyState.value = 'checking'
  try {
    const response = await axios.get('/api/portal/auth/password-reset/verify', {
      params: { token: token.value }
    })
    verifyState.value = response.data?.valid ? 'valid' : 'invalid'
  } catch {
    // 网络错误与 5xx 一律按「无法确认」处理，绝不当成链接失效
    verifyState.value = 'unavailable'
  }
}

onMounted(checkToken)

const heading = computed(() => {
  if (!hasToken.value) return '找回密码'
  // 校验中仍显示「设置新密码」：先闪一下「已失效」再变回表单是最糟的观感
  return verifyState.value === 'invalid' ? '链接已失效' : '设置新密码'
})

const subtitle = computed(() => {
  if (!hasToken.value) return '输入注册时使用的邮箱，我们会发送一条重置链接。'
  if (verifyState.value === 'invalid') return '这条重置链接已过期或已被使用。'
  if (verifyState.value === 'unavailable') return '暂时无法确认链接状态，请稍后重试。'
  return '重置链接有效期 15 分钟，且只能使用一次。'
})

const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const errorMessage = ref('')
const serverMessage = ref('')

// 服务端「同邮箱 60 秒冷却」的前端镜像，纯体验优化：避免用户连点。
// 注意它不能让用户误以为服务端会报错——被限流时服务端仍然返回统一成功文案。
const cooldown = ref(0)
let cooldownTimer: ReturnType<typeof setInterval> | null = null

const stopCooldown = () => {
  if (cooldownTimer !== null) {
    clearInterval(cooldownTimer)
    cooldownTimer = null
  }
}

const startCooldown = () => {
  cooldown.value = 60
  stopCooldown()
  cooldownTimer = setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) {
      cooldown.value = 0
      stopCooldown()
    }
  }, 1000)
}

onUnmounted(stopCooldown)

const submitRequest = async () => {
  errorMessage.value = ''
  serverMessage.value = ''
  loading.value = true
  try {
    const response = await axios.post('/api/portal/auth/password-reset/request', {
      email: email.value.trim()
    })
    serverMessage.value =
      response.data?.message || '如果该邮箱已注册，我们已发送重置邮件'
    startCooldown()
  } catch (e: any) {
    errorMessage.value = e.response?.data?.detail || '请求失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

const submitConfirm = async () => {
  errorMessage.value = ''
  serverMessage.value = ''
  if (password.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    const response = await axios.post('/api/portal/auth/password-reset/confirm', {
      token: token.value,
      password: password.value
    })
    // 重置成功时服务端已吊销该用户的全部会话（包括可能正开着这台设备的那个），
    // 因此顺手清掉本地身份快照，再回登录页用新密码重新认证。
    clearUserSession()
    serverMessage.value = response.data?.message || '密码重置成功，请使用新密码登录'
    window.setTimeout(() => router.push('/login'), 1500)
  } catch (e: any) {
    errorMessage.value = e.response?.data?.detail || '重置失败，请重新申请'
  } finally {
    loading.value = false
  }
}

const backToEmailMode = () => {
  errorMessage.value = ''
  serverMessage.value = ''
  // 用 replace 而不是 push：让「链接已失效」的地址不留在历史里，避免反复点到
  router.replace('/reset-password')
}
</script>

<template>
  <div
    class="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 px-4 py-10"
  >
    <div class="w-full max-w-md bg-white rounded-2xl shadow-2xl p-8">
      <!-- 顶部循环文案。固定一行高度：逐字增减会让行宽不断变化，若不锁高，
           下面的标题与输入框会跟着上下抖动。aria-hidden 是因为对屏幕阅读器而言，
           一句不断被删掉重打的话只是噪音，真正的信息都在下方静态说明里。 -->
      <p
        aria-hidden="true"
        class="mb-3 flex min-h-[1.5rem] items-center text-[13px] leading-relaxed tracking-wide text-slate-400"
      >
        <span>{{ ambientText }}</span>
        <span class="ml-0.5 inline-block h-3 w-[2px] shrink-0 animate-pulse rounded-full bg-slate-300"></span>
      </p>

      <div class="mb-6">
        <h1 class="text-xl font-bold text-slate-900">{{ heading }}</h1>
        <p class="mt-1 text-xs text-slate-500">{{ subtitle }}</p>
      </div>

      <div
        v-if="serverMessage"
        class="mb-5 rounded-lg bg-green-50 p-3 text-[11px] leading-relaxed text-green-700"
      >
        <p>{{ serverMessage }}</p>
        <!-- 防枚举的必要补偿：服务端不会告诉用户「这个邮箱没注册」，
             因此这里给出自查方向，避免用户对着一个不存在的邮箱干等。 -->
        <p v-if="!hasToken" class="mt-1 text-green-600">
          如果长时间未收到，请检查邮箱地址是否正确，并查看垃圾邮件。
        </p>
      </div>

      <div
        v-if="errorMessage"
        class="mb-5 rounded-lg bg-red-50 p-3 text-[11px] text-red-600"
      >
        {{ errorMessage }}
      </div>

      <!-- 无 token：申请重置链接 -->
      <form v-if="!hasToken" class="space-y-4" @submit.prevent="submitRequest">
        <div class="space-y-1.5">
          <label class="ml-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
            邮箱
          </label>
          <input
            v-model="email"
            type="email"
            required
            autocomplete="email"
            class="w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none transition-all focus:border-blue-500 disabled:bg-gray-100"
            placeholder="you@example.com"
            :disabled="loading || cooldown > 0"
          />
        </div>
        <button
          type="submit"
          class="flex w-full items-center justify-center rounded-lg bg-blue-600 py-3 text-sm font-bold text-white shadow-lg shadow-blue-600/10 transition-all hover:bg-blue-700 active:scale-[0.98] disabled:opacity-70"
          :disabled="loading || cooldown > 0"
        >
          <span
            v-if="loading"
            class="mr-3 h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
          ></span>
          {{ cooldown > 0 ? `请稍候 ${cooldown} 秒` : loading ? '发送中...' : '发送重置链接' }}
        </button>
      </form>

      <!-- 有 token：先按预校验结果分流，绝不做「先给表单、再把它抽走」 -->
      <template v-else>
        <!-- 校验中：给骨架占位，避免用户刚敲两个字符就被换成「已失效」 -->
        <div v-if="verifyState === 'checking'" class="space-y-3">
          <div class="h-[42px] animate-pulse rounded-lg bg-slate-100"></div>
          <div class="h-[42px] animate-pulse rounded-lg bg-slate-100"></div>
          <div class="h-[46px] animate-pulse rounded-lg bg-slate-100"></div>
          <p class="text-center text-[11px] text-slate-400">正在确认链接状态…</p>
        </div>

        <!-- 链接失效：直接给重新申请入口，不再让用户白填一遍密码 -->
        <div v-else-if="verifyState === 'invalid'" class="space-y-4">
          <div class="rounded-lg bg-amber-50 p-3 text-[11px] leading-relaxed text-amber-700">
            <p>重置链接已失效：可能超过了 15 分钟有效期，或者已经被使用过。</p>
            <p class="mt-1">重新申请一条新链接即可继续，旧链接会同时作废。</p>
          </div>
          <button
            type="button"
            class="w-full rounded-lg bg-blue-600 py-3 text-sm font-bold text-white shadow-lg shadow-blue-600/10 transition-all hover:bg-blue-700 active:scale-[0.98]"
            @click="backToEmailMode"
          >
            重新申请重置链接
          </button>
        </div>

        <!-- 服务不可用 / 网络异常：绝不能报成「链接失效」，否则用户白申请一封邮件 -->
        <div v-else-if="verifyState === 'unavailable'" class="space-y-4">
          <div class="rounded-lg bg-red-50 p-3 text-[11px] leading-relaxed text-red-600">
            暂时无法确认链接状态，可能是网络或服务波动。链接本身未必失效，请稍后重试。
          </div>
          <button
            type="button"
            class="w-full rounded-lg border border-slate-200 py-3 text-sm font-bold text-slate-600 transition-all hover:border-blue-500 hover:text-blue-600 active:scale-[0.98]"
            @click="checkToken"
          >
            重试
          </button>
        </div>

        <form v-else class="space-y-4" @submit.prevent="submitConfirm">
        <div class="space-y-1.5">
          <label class="ml-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
            新密码
          </label>
          <input
            v-model="password"
            type="password"
            required
            autocomplete="new-password"
            class="w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none transition-all focus:border-blue-500 disabled:bg-gray-100"
            placeholder="8-32 位，含大小写字母/数字/符号中的至少三类"
            :disabled="loading"
          />
        </div>
        <div class="space-y-1.5">
          <label class="ml-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
            确认新密码
          </label>
          <input
            v-model="confirmPassword"
            type="password"
            required
            autocomplete="new-password"
            class="w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none transition-all focus:border-blue-500 disabled:bg-gray-100"
            placeholder="再次输入新密码"
            :disabled="loading"
          />
        </div>
        <button
          type="submit"
          class="flex w-full items-center justify-center rounded-lg bg-blue-600 py-3 text-sm font-bold text-white shadow-lg shadow-blue-600/10 transition-all hover:bg-blue-700 active:scale-[0.98] disabled:opacity-70"
          :disabled="loading"
        >
          <span
            v-if="loading"
            class="mr-3 h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
          ></span>
          {{ loading ? '提交中...' : '重置密码' }}
        </button>
        <button
          type="button"
          class="w-full text-center text-xs text-slate-500 transition-colors hover:text-blue-600"
          @click="backToEmailMode"
        >
          链接已失效？重新申请
        </button>
      </form>
      </template>

      <div class="mt-6 text-center">
        <router-link to="/login" class="text-xs text-blue-600 hover:underline">
          返回登录
        </router-link>
      </div>
    </div>
  </div>
</template>
