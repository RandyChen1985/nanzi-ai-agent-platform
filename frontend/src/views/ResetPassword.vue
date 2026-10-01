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
import { computed, onUnmounted, ref } from 'vue'
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
 * 不是传达指引。真正要用户读到的约束（30 分钟有效期、只能用一次）留在标题下方那行
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
        <h1 class="text-xl font-bold text-slate-900">
          {{ hasToken ? '设置新密码' : '找回密码' }}
        </h1>
        <p class="mt-1 text-xs text-slate-500">
          <template v-if="hasToken">重置链接有效期 30 分钟，且只能使用一次。</template>
          <template v-else>输入注册时使用的邮箱，我们会发送一条重置链接。</template>
        </p>
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

      <!-- 有 token：设置新密码 -->
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

      <div class="mt-6 text-center">
        <router-link to="/login" class="text-xs text-blue-600 hover:underline">
          返回登录
        </router-link>
      </div>
    </div>
  </div>
</template>
