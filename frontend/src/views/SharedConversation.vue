<template>
  <div class="min-h-screen bg-[#f6f7fb] text-gray-900">
    <!-- 加载 -->
    <div v-if="phase === 'loading'" class="mx-auto flex min-h-screen max-w-[860px] flex-col gap-3 px-4 py-10">
      <div class="h-6 w-2/5 animate-pulse rounded-lg bg-gray-200"></div>
      <div class="h-3 w-1/4 animate-pulse rounded bg-gray-200"></div>
      <div class="mt-6 h-24 animate-pulse rounded-2xl bg-white"></div>
      <div class="h-40 animate-pulse rounded-2xl bg-white"></div>
      <p class="mt-2 text-center text-[11.5px] text-gray-400">正在加载分享内容…</p>
    </div>

    <!-- 失效 / 出错 -->
    <div v-else-if="phase === 'error'" class="grid min-h-screen place-items-center px-4">
      <div class="w-full max-w-[420px] rounded-2xl border border-gray-100 bg-white px-6 py-9 text-center shadow-sm">
        <div class="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-gray-100 text-gray-400">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.5" />
          </svg>
        </div>
        <h1 class="text-[15px] font-extrabold text-gray-900">链接不存在或已失效</h1>
        <p class="mt-1.5 text-[11.5px] leading-relaxed text-gray-400">
          {{ errorMessage || '分享可能已被作废、已过期，或链接地址不完整。' }}
        </p>
      </div>
    </div>

    <!-- 需要密码 -->
    <div v-else-if="phase === 'locked'" class="grid min-h-screen place-items-center px-4">
      <form class="w-full max-w-[400px] rounded-2xl border border-gray-100 bg-white px-6 py-7 shadow-sm" @submit.prevent="submitSecret">
        <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-primary/10 text-primary">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="4.5" y="10" width="15" height="10.5" rx="2.5" /><path d="M8.5 10V7a3.5 3.5 0 017 0v3" />
          </svg>
        </div>
        <h1 class="text-center text-[15px] font-extrabold text-gray-900">这是一个需要密码的分享</h1>
        <p class="mt-1.5 line-clamp-2 text-center text-[11.5px] leading-relaxed text-gray-400">
          {{ meta?.title || '对方给你分享了一段会话' }}
        </p>

        <input
          v-model="secret"
          type="password"
          autocomplete="one-time-code"
          placeholder="请输入访问密码"
          class="mt-4 h-10 w-full rounded-[9px] border border-gray-200 px-3 text-center font-mono text-[14px] tracking-[0.18em] outline-none focus:border-primary"
        />
        <p v-if="unlockError" class="mt-2 text-center text-[11px] text-red-500">{{ unlockError }}</p>

        <button
          type="submit"
          class="mt-3 h-10 w-full rounded-lg bg-primary text-[13px] font-bold text-white disabled:opacity-60"
          :disabled="unlocking || !secret.trim()"
        >
          {{ unlocking ? '验证中…' : '查看会话' }}
        </button>

        <p v-if="meta?.owner_display" class="mt-3 text-center text-[10.5px] text-gray-400">
          由 {{ meta.owner_display }} 分享<template v-if="meta?.expires_at"> · {{ expiryLabel }}</template>
        </p>
      </form>
    </div>

    <!-- 正文 -->
    <div v-else-if="phase === 'ready'" class="pb-20">
      <header class="sticky top-0 z-10 border-b border-gray-100 bg-white/85 backdrop-blur">
        <div class="mx-auto max-w-[860px] px-4 py-3.5">
          <div class="flex items-start gap-2">
            <div class="min-w-0">
              <h1 class="line-clamp-1 text-[14.5px] font-extrabold text-gray-900">{{ content?.title || '会话分享' }}</h1>
              <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[10.5px] text-gray-400">
                <span v-if="content?.agent_name">来自 {{ content.agent_name }}</span>
                <span v-if="content?.agent_name">·</span>
                <span>{{ messageTurns }} 轮问答</span>
                <span>·</span>
                <span>{{ expiryLabel }}</span>
              </p>
            </div>
          </div>
        </div>
      </header>

      <main class="mx-auto max-w-[860px] px-4 py-5">
        <div class="space-y-4">
          <div v-for="(message, index) in content?.messages || []" :key="index" class="flex gap-2.5" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
            <div v-if="message.role === 'assistant'" class="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/10 text-[10px] font-black text-primary">
              AI
            </div>
            <div
              class="min-w-0"
              :class="message.role === 'user'
                ? 'max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words'
                : 'max-w-[calc(100%-2rem)] flex-1 rounded-2xl rounded-bl-md border border-gray-100 bg-white px-3.5 py-3 shadow-sm'"
            >
              <div v-if="message.role === 'assistant'" class="markdown-body text-[13px] leading-relaxed text-gray-800">
                <MessageRenderer
                  :content="message.content"
                  theme="default"
                  hide-quick-buttons
                  :readonly="true"
                />
              </div>
              <template v-else>{{ message.content }}</template>
            </div>
          </div>
        </div>

        <div v-if="(content?.messages || []).length === 0" class="py-16 text-center text-[12px] text-gray-400">
          这个会话没有可展示的正文
        </div>
      </main>

      <footer class="fixed inset-x-0 bottom-0 border-t border-gray-100 bg-white/90 backdrop-blur">
        <div class="mx-auto flex max-w-[860px] items-center justify-center gap-1.5 px-4 py-2.5 text-[10.5px] text-gray-400">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="4.5" y="10" width="15" height="10.5" rx="2.5" /><path d="M8.5 10V7a3.5 3.5 0 017 0v3" />
          </svg>
          只读快照 · 内容在分享创建时冻结，不会随原会话更新
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 公开只读分享页。
 *
 * 加载顺序刻意分三步：先取元信息（不带内容、不计数），再判断要不要密码，最后才取正文。
 * 令牌存 sessionStorage 而不是 URL query——URL 会进浏览器历史、Referer 和访问日志。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchShareContent,
  fetchShareMeta,
  unlockShare,
  type ShareContent,
  type ShareMeta,
} from '@/api/chatShare'
import { formatRemaining } from '@/utils/shareExpiry'

// echarts / mermaid 体积大，渲染内核必须懒加载，不能压进分享页首屏包
const MessageRenderer = defineAsyncComponent(() => import('@/components/MessageRenderer.vue'))

type Phase = 'loading' | 'locked' | 'ready' | 'error'

const route = useRoute()
const shareId = computed(() => String(route.params.shareId || ''))

const phase = ref<Phase>('loading')
const meta = ref<ShareMeta | null>(null)
const content = ref<ShareContent | null>(null)
const secret = ref('')
const unlocking = ref(false)
const unlockError = ref('')
const errorMessage = ref('')

const tokenKey = computed(() => `share_token:${shareId.value}`)

const messageTurns = computed(() => {
  const total = (content.value?.messages || []).filter((m) => m.role === 'user').length
  return total || meta.value?.turn_count || 0
})

const expiryLabel = computed(() => {
  if (!meta.value?.expires_at) return '永久有效'
  return formatRemaining(meta.value.expires_at)
})

function readToken(): string {
  try {
    return sessionStorage.getItem(tokenKey.value) || ''
  } catch {
    return ''
  }
}

function writeToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(tokenKey.value, token)
    else sessionStorage.removeItem(tokenKey.value)
  } catch {
    /* 隐私模式下 storage 可能不可用：退化为仅本次内存有效 */
  }
}

function markError(message: string): void {
  errorMessage.value = message
  phase.value = 'error'
}

async function loadContent(token?: string): Promise<void> {
  try {
    content.value = await fetchShareContent(shareId.value, token)
    phase.value = 'ready'
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 401) {
      // 缓存的令牌可能已过期：清掉后回到密码卡片，而不是直接判失败
      writeToken('')
      phase.value = 'locked'
      return
    }
    if (status === 404) {
      markError('分享可能已被作废或已过期。')
      return
    }
    markError('加载失败，请稍后重试。')
  }
}

async function boot(): Promise<void> {
  if (!shareId.value) {
    markError('链接地址不完整。')
    return
  }
  try {
    meta.value = await fetchShareMeta(shareId.value)
  } catch (error: any) {
    const status = error?.response?.status
    markError(status === 404 ? '分享可能已被作废或已过期。' : '加载失败，请稍后重试。')
    return
  }

  if (meta.value.access_mode === 'public') {
    await loadContent()
    return
  }

  // 设密分享：先试上次解锁留下的令牌，401 才落回密码卡片
  const cached = readToken()
  if (cached) {
    await loadContent(cached)
    return
  }
  phase.value = 'locked'
}

async function submitSecret(): Promise<void> {
  if (!secret.value.trim() || unlocking.value) return
  unlocking.value = true
  unlockError.value = ''
  try {
    const result = await unlockShare(shareId.value, secret.value.trim())
    writeToken(result.token)
    await loadContent(result.token)
    secret.value = ''
  } catch (error: any) {
    const status = error?.response?.status
    unlockError.value =
      status === 429 ? '尝试次数过多，请稍后再试'
        : status === 404 ? '分享已失效'
          : '密码不正确，请重新输入'
  } finally {
    unlocking.value = false
  }
}

onMounted(() => {
  void boot()
})
</script>

<style scoped>
/* 分享页固定浅色：公开链接不跟随访客的系统主题，避免深色下 Markdown 对比度失控 */
:deep(.markdown-body) {
  color: #1f2937;
  word-break: break-word;
}
:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
}
:deep(.markdown-body th),
:deep(.markdown-body td) {
  border: 1px solid #e5e7eb;
  padding: 6px 10px;
  font-size: 12px;
}
</style>
