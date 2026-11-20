<template>
  <div class="min-h-screen bg-[#f6f7fb] text-gray-900">
    <!-- 加载 -->
    <div v-if="phase === 'loading'" class="mx-auto flex min-h-screen max-w-[520px] flex-col justify-center gap-3 px-4">
      <div class="h-24 animate-pulse rounded-2xl bg-white"></div>
      <div class="h-10 animate-pulse rounded-xl bg-white"></div>
      <p class="mt-1 text-center text-[11.5px] text-gray-400">正在加载分享内容…</p>
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
          {{ meta?.file_name || '对方给你分享了一个文件' }}
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
          {{ unlocking ? '验证中…' : '打开文件' }}
        </button>

        <p v-if="meta?.owner_display" class="mt-3 text-center text-[10.5px] text-gray-400">
          由 {{ meta.owner_display }} 分享<template v-if="meta?.expires_at"> · {{ expiryLabel }}</template>
        </p>
      </form>
    </div>

    <!-- 可在线预览：铺满视口的阅读/查看布局 -->
    <div v-else-if="isInlinePreview" class="flex h-screen flex-col">
      <header class="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-white px-4 py-2.5">
        <div class="flex min-w-0 items-center gap-2">
          <span class="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-gray-100 text-[14px]">{{ fileGlyph }}</span>
          <div class="min-w-0">
            <p class="truncate text-[13px] font-extrabold text-gray-900" :title="meta?.file_name">{{ meta?.file_name }}</p>
            <p class="truncate text-[10.5px] text-gray-400">
              {{ sizeLabel }}<template v-if="meta?.owner_display"> · 由 {{ meta.owner_display }} 分享</template>
              <template v-if="meta?.expires_at"> · {{ expiryLabel }}</template>
            </p>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button
            v-if="previewNotice"
            class="rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-[10.5px] font-bold text-amber-700"
            :title="previewNotice"
          >{{ previewNotice }}</button>
          <button
            class="flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1.5 text-[11.5px] font-bold text-white"
            @click="startDownload"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
              <path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14" />
            </svg>
            下载
          </button>
        </div>
      </header>

      <!--
        sandbox 里刻意**没有** allow-same-origin：给了它，iframe 就与应用同源，
        里面的脚本能读你的登录凭证。只给 allow-scripts 时文档处于不透明源，脚本能跑
        但拿不到任何平台身份。后端响应头还会再带一次 CSP sandbox 兜底。
      -->
      <iframe
        v-if="kind === 'html'"
        :key="rawUrl"
        :src="rawUrl"
        class="flex-1 w-full border-0 bg-white"
        :title="meta?.file_name || '预览'"
        sandbox="allow-scripts allow-forms"
        referrerpolicy="no-referrer"
      ></iframe>

      <!--
        PDF 单独一个 iframe 且**不加 sandbox**：Chrome 的内置 PDF 阅读器在无
        allow-plugins 的沙箱里会直接白屏。PDF 内容跑在浏览器自己的阅读器里，
        拿不到本应用的登录态，不需要这层。
      -->
      <iframe
        v-else-if="kind === 'pdf'"
        :key="rawUrl"
        :src="rawUrl"
        class="flex-1 w-full border-0 bg-white"
        :title="meta?.file_name || 'PDF 预览'"
        referrerpolicy="no-referrer"
      ></iframe>

      <!-- svg 用 <img>：img 里的 svg 脚本不执行，比 iframe 更安全也更轻 -->
      <div v-else-if="kind === 'image' || kind === 'svg'" class="flex flex-1 items-center justify-center overflow-auto bg-[#f8fafc] p-4">
        <img
          :src="rawUrl"
          :alt="meta?.file_name || 'image'"
          class="max-h-full max-w-full rounded-lg bg-white shadow-sm"
        />
      </div>

      <!-- 文本类：markdown / csv / 代码 -->
      <div v-else class="flex-1 overflow-auto bg-white">
        <div v-if="textLoading" class="mx-auto max-w-[860px] px-5 py-8">
          <div class="h-5 w-2/5 animate-pulse rounded bg-gray-200"></div>
          <div class="mt-3 h-3 w-4/5 animate-pulse rounded bg-gray-100"></div>
          <div class="mt-2 h-3 w-3/5 animate-pulse rounded bg-gray-100"></div>
        </div>

        <div v-else-if="textError" class="mx-auto max-w-[860px] px-5 py-10 text-center">
          <p class="text-[12px] text-red-500">{{ textError }}</p>
          <button class="mt-2 text-[12px] font-semibold text-primary" @click="loadText()">重试</button>
        </div>

        <div v-else class="mx-auto max-w-[860px] px-5 py-6">
          <div v-if="isMarkdown" class="markdown-body text-[13.5px] leading-relaxed text-gray-800">
            <MessageRenderer :content="textContent" theme="default" hide-quick-buttons :readonly="true" />
          </div>

          <div v-else-if="isCsv" class="overflow-auto">
            <table class="w-full border-collapse text-[12px]">
              <thead>
                <tr>
                  <th v-for="(cell, i) in csvRows[0] || []" :key="i" class="border border-gray-200 bg-gray-50 px-2 py-1.5 text-left font-bold">{{ cell }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in csvRows.slice(1)" :key="ri">
                  <td v-for="(cell, ci) in row" :key="ci" class="border border-gray-200 px-2 py-1.5">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <pre v-else class="overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-[1.7] text-gray-800">{{ textContent }}</pre>

          <p v-if="truncated" class="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11.5px] text-amber-700">
            文件较大，这里只显示前 {{ Math.round((maxBytes || 0) / 1024 / 1024) }} MB，完整内容请下载查看。
          </p>
        </div>
      </div>
    </div>

    <!-- 不可预览：只给下载 -->
    <div v-else class="grid min-h-screen place-items-center px-4">
      <div class="w-full max-w-[460px] rounded-2xl border border-gray-100 bg-white px-6 py-8 shadow-sm">
        <div class="flex items-center gap-3">
          <div class="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-emerald-50 text-[20px]">
            {{ fileGlyph }}
          </div>
          <div class="min-w-0">
            <h1 class="truncate text-[14px] font-extrabold text-gray-900">{{ meta?.file_name }}</h1>
            <p class="mt-1 text-[11px] text-gray-500">
              {{ sizeLabel }}<template v-if="meta?.owner_display"> · 由 {{ meta.owner_display }} 分享</template>
            </p>
          </div>
        </div>

        <button class="mt-5 h-10 w-full rounded-lg bg-primary text-[13px] font-bold text-white" @click="startDownload">
          下载文件
        </button>

        <p class="mt-3 text-center text-[10.5px] leading-relaxed text-gray-400">
          <template v-if="meta?.expires_at">本链接 {{ expiryLabel }}，过期后自动失效</template>
          <template v-else>本链接长期有效，分享人可随时作废</template>
        </p>
        <p v-if="downloadHint" class="mt-2 text-center text-[11px] font-semibold text-amber-600">{{ downloadHint }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 文件分享落地页（访客视角，免登录）。
 *
 * 呈现方式按 ``meta.preview_kind`` 分流，与「下载」并行提供：
 *
 * | kind | 呈现 |
 * |---|---|
 * | html | 沙箱 iframe 直接渲染（生成的应用、报告页可用） |
 * | pdf  | iframe 内嵌浏览器 PDF 阅读器 |
 * | image | `<img>` 直接显示 |
 * | svg  | `<img>` 显示——img 里的 svg **脚本不执行**，比 iframe 更安全 |
 * | text | 拉 `/text` 后渲染：markdown 复用消息渲染器，csv 转表格，其余 `<pre>` |
 * | download | 只给下载（Office / 压缩包等无法在浏览器里正常显示） |
 *
 * 安全边界全在后端：所有响应带 `X-Content-Type-Options: nosniff`，html/svg 额外带
 * `Content-Security-Policy: sandbox`。这里的 `sandbox` 属性是第二道，用来防「后端头被
 * 中间层摘掉」；反过来访客直接打开 raw 地址时，靠的是后端那道。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  downloadSharedFileUrl,
  fetchFileShareMeta,
  fetchFileShareText,
  sharedFileRawUrl,
  unlockFileShare,
  type FileShareMeta,
} from '@/api/fileShare'
import { formatInPlatformTimezone } from '@/utils/platformTimezone'

// markdown 渲染含 echarts/mermaid 等重依赖，落地页首屏不该背着它们
const MessageRenderer = defineAsyncComponent(() => import('@/components/MessageRenderer.vue'))

type Phase = 'loading' | 'locked' | 'ready' | 'error'

const route = useRoute()
const shareId = computed(() => String(route.params.shareId || ''))
const tokenKey = computed(() => `file_share_token:${shareId.value}`)

const phase = ref<Phase>('loading')
const meta = ref<FileShareMeta | null>(null)
const secret = ref('')
const unlocking = ref(false)
const unlockError = ref('')
const errorMessage = ref('')
const downloadHint = ref('')

const textContent = ref('')
const textLoading = ref(false)
const textError = ref('')
const truncated = ref(false)
const maxBytes = ref(0)

const kind = computed(() => meta.value?.preview_kind || 'download')
const isInlinePreview = computed(() =>
  ['html', 'pdf', 'image', 'svg', 'text'].includes(kind.value),
)
const previewNotice = computed(() => {
  if (kind.value === 'html') return '沙箱预览'
  if (kind.value === 'svg') return '图片预览'
  return ''
})

const rawUrl = computed(() => sharedFileRawUrl(shareId.value, readToken() || undefined))

const isMarkdown = computed(() => ['.md', '.markdown'].includes(ext()))
const isCsv = computed(() => ['.csv', '.tsv'].includes(ext()))

function ext(): string {
  return String(meta.value?.file_ext || '').toLowerCase()
}

const sizeLabel = computed(() => {
  const size = Number(meta.value?.file_size || 0)
  if (!(size > 0)) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
})

const fileGlyph = computed(() => {
  switch (kind.value) {
    case 'html':
      return '🌐'
    case 'pdf':
      return '📕'
    case 'image':
    case 'svg':
      return '🖼️'
    case 'text':
      return ['.md', '.markdown'].includes(ext()) ? '📄' : '📃'
    default:
      if (['.zip', '.tar', '.gz', '.7z', '.rar'].includes(ext())) return '🗜️'
      if (['.xlsx', '.xls', '.csv'].includes(ext())) return '📊'
      if (['.docx', '.doc'].includes(ext())) return '📝'
      if (['.pptx', '.ppt'].includes(ext())) return '📽️'
      return '📎'
  }
})

const expiryLabel = computed(() => {
  const raw = meta.value?.expires_at
  if (!raw) return '长期有效'
  return `${formatInPlatformTimezone(new Date(raw))} 失效`
})

/** CSV/TSV 极简解析：支持双引号包裹与转义，够看报表；格式再怪就走 <pre>。 */
const csvRows = computed<string[][]>(() => {
  const delimiter = ext() === '.tsv' ? '\t' : ','
  const text = textContent.value
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"'
          i += 1
        } else {
          quoted = false
        }
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"') {
      quoted = true
    } else if (ch === delimiter) {
      row.push(cell)
      cell = ''
    } else if (ch === '\n') {
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else if (ch !== '\r') {
      cell += ch
    }
  }
  if (cell || row.length) {
    row.push(cell)
    rows.push(row)
  }
  return rows
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

type ProbeResult = 'ok' | 'unauthorized' | 'gone' | 'changed' | 'unreachable'

/**
 * 用 HEAD 探测：令牌还有效吗、文件还是分享时那一版吗。
 *
 * 必须用 HEAD 而不是 GET：GET 会在后端计一次预览/下载，落地页多刷几次计数就虚高
 * （后端已把 HEAD 排除在计数之外）。
 */
async function probeReadable(token?: string): Promise<ProbeResult> {
  try {
    const resp = await fetch(sharedFileRawUrl(shareId.value, token), {
      method: 'HEAD',
      credentials: 'include',
    })
    if (resp.status === 401) return 'unauthorized'
    if (resp.status === 404) return 'gone'
    if (resp.status === 410) return 'changed'
    return resp.ok ? 'ok' : 'unreachable'
  } catch {
    return 'unreachable'
  }
}

async function loadText(): Promise<void> {
  textLoading.value = true
  textError.value = ''
  try {
    const result = await fetchFileShareText(shareId.value, readToken() || undefined)
    textContent.value = result.content
    truncated.value = !!result.truncated
    maxBytes.value = Number(result.max_bytes || 0)
    if (result.file) meta.value = { ...meta.value, ...result.file }
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 401) {
      writeToken('')
      phase.value = 'locked'
      return
    }
    textError.value = status === 410 ? '文件已更新，请让分享人重新分享' : '内容加载失败，请稍后重试'
  } finally {
    textLoading.value = false
  }
}

async function boot(): Promise<void> {
  if (!shareId.value) {
    markError('链接地址不完整。')
    return
  }
  try {
    meta.value = await fetchFileShareMeta(shareId.value)
  } catch (error: any) {
    const status = error?.response?.status
    markError(status === 404 ? '分享可能已被作废或已过期。' : '加载失败，请稍后重试。')
    return
  }

  if (meta.value.access_mode === 'public') {
    phase.value = 'ready'
    if (meta.value.preview_kind === 'text') void loadText()
    return
  }

  // 设密分享：先试上次解锁留下的令牌，失效才落回密码卡片
  const cached = readToken()
  if (cached) {
    const probe = await probeReadable(cached)
    if (probe === 'ok') {
      phase.value = 'ready'
      if (meta.value.preview_kind === 'text') void loadText()
      return
    }
    if (probe === 'changed') downloadHint.value = '文件已更新，请让分享人重新分享'
    if (probe === 'gone') {
      markError('分享可能已被作废或已过期。')
      return
    }
    writeToken('')
  }
  phase.value = 'locked'
}

async function submitSecret(): Promise<void> {
  if (!secret.value.trim() || unlocking.value) return
  unlocking.value = true
  unlockError.value = ''
  try {
    const result = await unlockFileShare(shareId.value, secret.value.trim())
    writeToken(result.token)
    secret.value = ''
    phase.value = 'ready'
    // 文本类要再拉一次内容；不可预览的格式直接发起下载，别让访客再点一次
    if (meta.value?.preview_kind === 'text') {
      void loadText()
    } else if (!isInlinePreview.value) {
      void startDownload()
    }
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

/**
 * 先 HEAD 探测再下载：直接点 `<a download>` 的话，一旦文件已被改写（410）或令牌过期（401），
 * 浏览器会把这个 JSON 错误页当下载内容存下来，访客只会得到一个坏文件。
 */
async function startDownload(): Promise<void> {
  downloadHint.value = ''
  const token = readToken() || undefined
  const probe = await probeReadable(token)
  if (probe === 'unauthorized') {
    writeToken('')
    phase.value = 'locked'
    return
  }
  if (probe === 'changed') {
    downloadHint.value = '文件已更新，请让分享人重新分享'
    return
  }
  if (probe === 'gone') {
    markError('分享可能已被作废或已过期。')
    return
  }
  if (probe === 'unreachable') {
    downloadHint.value = '暂时无法下载，请稍后重试'
    return
  }

  const link = document.createElement('a')
  link.href = downloadSharedFileUrl(shareId.value, token)
  link.rel = 'noopener'
  link.download = meta.value?.file_name || 'download'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
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
