<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { toolApi, type SysApiToolPreviewResult } from '../../api/tool'
import { parseToolSchema } from '../../utils/toolSchema'
import CopyButton from '../common/CopyButton.vue'
import { PlayIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  method: string
  urlTemplate: string
  headersStr: string
  schemaStr: string
  signingEnabled?: boolean
  signingAlgorithm?: string
  signingKeyId?: string | null
  signingSecret?: string
  toolId?: string | null
  /** 从列表快捷试跑时自动拉开抽屉 */
  autoOpen?: boolean
  /** 快捷试跑时不渲染“打开试跑”卡片，只保留抽屉 */
  hideTrigger?: boolean
  /** 抽屉里标识当前工具，避免从列表进来时不知道在试哪个工具 */
  toolLabel?: string
}>()

const emit = defineEmits<{ (e: 'closed'): void }>()

const values = ref<Record<string, string>>({})
const loading = ref(false)
const result = ref<SysApiToolPreviewResult | null>(null)
const resultStale = ref(false)
const bodyView = ref<'pretty' | 'raw'>('pretty')
const failure = ref<string>('')
const showDrawer = ref(false)
const triggerButton = ref<HTMLButtonElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
let previousBodyOverflow = ''

const parsedSchema = computed(() => parseToolSchema(props.schemaStr))
const params = computed(() => parsedSchema.value?.params ?? [])
const systemParams = computed(() => parsedSchema.value?.systemParams ?? [])

/** 私网 / 回环 / 链路本地：**只提示，不阻止**（与运行时同权的既定决策）。 */
const isPrivateTarget = computed(() => {
  const matched = props.urlTemplate.match(/^[a-zA-Z][a-zA-Z0-9+.\-]*:\/\/([^/?#]+)/)
  if (!matched) return false
  const authority = (matched[1] ?? '').split('@').pop() || ''
  const host = (authority.split(':')[0] ?? '').toLowerCase()
  if (!host) return false
  if (host === '[::1]' || host === '::1') return true
  if (host === 'localhost' || host.endsWith('.local') || host.endsWith('.internal')) return true
  if (/^(10|127)\./.test(host)) return true
  if (/^192\.168\./.test(host)) return true
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return true
  if (/^169\.254\./.test(host)) return true
  return false
})

/** 换工具、换地址、换参数定义都要清掉旧结论：留着上一次的 200 会误导。 */
watch(() => [props.method, props.urlTemplate, props.headersStr, props.schemaStr, props.toolId], () => {
  const next: Record<string, string> = {}
  for (const param of params.value) {
    if (!param.required && param.defaultValue) next[param.name] = param.defaultValue
  }
  values.value = next
  result.value = null
  resultStale.value = false
  failure.value = ''
}, { immediate: true })

// 测试值、签名配置变了，上一次结果仍然有参考价值，但必须明确标成「过期」，
// 不能让用户把旧的 200 当成当前配置的结论。
watch(values, () => {
  failure.value = ''
  if (result.value) resultStale.value = true
}, { deep: true })

watch(
  () => [props.signingEnabled, props.signingKeyId, props.signingSecret, props.signingAlgorithm],
  () => {
    failure.value = ''
    if (result.value) resultStale.value = true
  },
)

watch(result, (value) => {
  if (value) bodyView.value = 'pretty'
})

const missingRequired = computed(() => params.value
  .filter((param) => param.required && !String(values.value[param.name] ?? '').trim())
  .map((param) => param.name))

const openDrawer = async () => {
  if (showDrawer.value) return
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  showDrawer.value = true
  await nextTick()
  closeButton.value?.focus()
}

const closeDrawer = async () => {
  if (!showDrawer.value) return
  showDrawer.value = false
  document.body.style.overflow = previousBodyOverflow
  await nextTick()
  triggerButton.value?.focus()
  emit('closed')
}

const handleDrawerKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Escape' || !showDrawer.value) return
  event.stopPropagation()
  void closeDrawer()
}

watch(showDrawer, (open) => {
  if (open) window.addEventListener('keydown', handleDrawerKeydown)
  else window.removeEventListener('keydown', handleDrawerKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleDrawerKeydown)
  if (showDrawer.value) document.body.style.overflow = previousBodyOverflow
})

onMounted(() => {
  if (props.autoOpen) void openDrawer()
})

const runPreview = async () => {
  failure.value = ''
  result.value = null
  resultStale.value = false

  if (!props.urlTemplate.trim()) {
    failure.value = '还没有填请求地址'
    return
  }
  if (missingRequired.value.length > 0) {
    // 后端也会拦一次，但先在本地说清楚缺哪个，避免一次无谓往返
    failure.value = `请先填写必填参数：${missingRequired.value.join('、')}`
    return
  }
  if (props.signingEnabled && !props.signingSecret && !props.toolId) {
    failure.value = '请求签名已开启，但没有可用的签名密钥；请先生成密钥，或先保存工具再试跑'
    return
  }

  let headers: Record<string, string> = {}
  try {
    headers = JSON.parse(props.headersStr || '{}')
  } catch {
    failure.value = 'Headers 不是合法 JSON，请先修正后再试跑'
    return
  }
  let schema: Record<string, any> = {}
  try {
    schema = JSON.parse(props.schemaStr || '{}')
  } catch {
    failure.value = '参数定义不是合法 JSON，请先修正后再试跑'
    return
  }

  loading.value = true
  try {
    const res = await toolApi.preview({
      method: props.method,
      url_template: props.urlTemplate,
      headers,
      parameter_schema: schema,
      params: { ...values.value },
      signing_enabled: !!props.signingEnabled,
      signing_algorithm: props.signingAlgorithm || 'hmac-sha256',
      signing_key_id: props.signingKeyId || null,
      ...(props.signingSecret ? { signing_secret: props.signingSecret } : {}),
      tool_id: props.toolId || null,
    })
    result.value = res.data
    resultStale.value = false
  } catch (e: any) {
    failure.value = '试跑请求失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

const headline = computed(() => {
  const current = result.value
  if (!current) return ''
  if (current.ok) return `HTTP ${current.status_code}`
  if (current.error_kind === 'http_error') return `HTTP ${current.status_code}`
  return '没能完成这次请求'
})

const hasKeys = (value: Record<string, any> | null | undefined): boolean =>
  !!value && Object.keys(value).length > 0

const signingHeaderEntries = computed(() => {
  const headers = result.value?.request?.headers || {}
  const wanted = [
    'X-Nanzi-Signature-Version',
    'X-Nanzi-Key-Id',
    'X-Nanzi-Timestamp',
    'X-Nanzi-Nonce',
    'X-Nanzi-Signature',
  ]
  const lower = new Map(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]))
  return wanted
    .map((name) => [name, lower.get(name.toLowerCase())] as const)
    .filter((entry): entry is readonly [string, any] => entry[1] !== undefined)
})

const prettyJson = (value: any): string => {
  if (value === undefined || value === null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const responseBody = computed(() => {
  const body = result.value?.body || ''
  if (bodyView.value === 'raw') return body
  try {
    return JSON.stringify(JSON.parse(body), null, 2)
  } catch {
    return body
  }
})

const responseBodyIsJson = computed(() => {
  const body = result.value?.body || ''
  if (!body.trim()) return false
  try {
    JSON.parse(body)
    return true
  } catch {
    return false
  }
})

/** cURL 单引号转义：把内部的 ' 变成 '\''，保证复制到 shell 可直接执行。 */
const shellQuote = (value: string): string => `'${String(value).replace(/'/g, `'\\''`)}'`

const curlCommand = computed(() => {
  const request = result.value?.request
  if (!request) return ''
  const lines = [`curl -X ${request.method}`, `  ${shellQuote(request.url)}`]
  for (const [key, value] of Object.entries(request.headers || {})) {
    lines.push(`  -H ${shellQuote(`${key}: ${value}`)}`)
  }
  if (request.body !== null && request.body !== undefined) {
    const body = typeof request.body === 'string' ? request.body : JSON.stringify(request.body)
    lines.push(`  --data-raw ${shellQuote(body)}`)
  }
  return lines.join(' \\\n')
})
</script>

<template>
  <div class="space-y-3">
    <div v-if="!hideTrigger" class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-gradient-to-r from-slate-50 to-white px-3.5 py-3">
      <div class="flex min-w-0 items-start gap-2.5">
        <span class="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <PlayIcon class="h-4 w-4" />
        </span>
        <div class="min-w-0">
          <h4 class="text-sm font-bold text-gray-800">试跑一次</h4>
          <p class="mt-0.5 text-xs leading-relaxed text-gray-500">
            在右侧抽屉填写测试值，查看最终请求和响应；<b>测试值不会保存到工具配置里。</b>
          </p>
        </div>
      </div>
      <button
        ref="triggerButton"
        type="button"
        class="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-primary-dark"
        @click="openDrawer"
      >
        <PlayIcon class="h-3.5 w-3.5" />
        打开试跑
      </button>
    </div>

    <teleport to="body">
      <transition
        enter-active-class="transition-opacity ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="transition-opacity ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-if="showDrawer"
          class="fixed inset-0 z-[160] overflow-hidden"
          role="dialog"
          aria-modal="true"
          aria-label="试跑工具"
        >
          <div class="absolute inset-0 bg-gray-500/30 backdrop-blur-xs" @click="closeDrawer" />

          <transition
            enter-active-class="transform transition ease-out duration-300"
            enter-from-class="translate-x-full"
            enter-to-class="translate-x-0"
            leave-active-class="transform transition ease-in duration-200"
            leave-from-class="translate-x-0"
            leave-to-class="translate-x-full"
          >
            <aside
              v-if="showDrawer"
              class="absolute inset-y-0 right-0 flex min-h-0 w-full max-w-2xl flex-col border-l border-gray-200 bg-white shadow-2xl"
            >
              <div class="flex items-start justify-between gap-3 border-b border-gray-100 bg-gray-50/70 px-4 py-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-2 text-sm font-bold text-gray-800">
                    <PlayIcon class="h-4 w-4 text-primary" />
                    试跑一次
                    <span class="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">真实请求</span>
                    <span v-if="toolLabel" class="truncate font-mono text-[11px] font-normal text-gray-500">{{ toolLabel }}</span>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
                    测试值只用于这一次请求，不会保存到工具配置。
                  </p>
                </div>
                <button
                  ref="closeButton"
                  type="button"
                  class="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
                  title="关闭"
                  aria-label="关闭试跑抽屉"
                  @click="closeDrawer"
                >
                  <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div class="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-y-contain p-4">
                <div v-if="isPrivateTarget" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
                  目标地址指向内网或本机。试跑会由平台服务器发起请求，确认这确实是你要验证的接口。
                </div>

                <p v-if="params.length === 0" class="text-xs italic text-gray-400">
                  这个工具不需要输入参数，直接试跑即可。
                </p>
                <div v-else class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div v-for="param in params" :key="param.name" class="space-y-1">
                    <label class="block font-mono text-xs text-gray-700">
                      {{ param.name }}<span v-if="param.required" class="ml-0.5 text-red-500">*</span>
                    </label>
                    <input
                      v-model="values[param.name]"
                      class="w-full rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-xs outline-none transition-all focus:border-primary focus:ring-2 focus:ring-primary/20"
                      :placeholder="param.description || param.type"
                    />
                  </div>
                </div>

                <p
                  v-if="systemParams.length > 0"
                  class="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-[11px] leading-relaxed text-indigo-700"
                >
                  系统级参数：{{ systemParams.map((param) => param.name).join('、') }}
                  （平台自动注入，无需填写）
                </p>

                <p
                  v-if="signingEnabled"
                  class="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] leading-relaxed text-emerald-700"
                >
                  请求签名已开启：平台会对最终发出的请求附加 <code class="font-mono">X-Nanzi-*</code> 签名 Header。
                  <span v-if="toolId && !signingSecret">本次使用已保存的密钥。</span>
                  <span v-else-if="signingSecret">本次使用草稿里生成的新密钥。</span>
                </p>

                <div v-if="failure" class="flex items-start justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  <p class="min-w-0">{{ failure }}</p>
                  <CopyButton :text="failure" label="失败信息" />
                </div>

                <div v-if="resultStale" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                  测试值或签名配置已修改。下面仍是上一次试跑的结果，请重新试跑后再下结论。
                </div>

                <div v-if="result" class="overflow-hidden rounded-lg border" :class="result.ok ? 'border-green-200' : 'border-amber-200'">
                  <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2" :class="result.ok ? 'border-green-100 bg-green-50' : 'border-amber-100 bg-amber-50'">
                    <span
                      class="rounded px-1.5 py-0.5 text-[11px] font-bold"
                      :class="result.ok ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-800'"
                    >{{ headline }}</span>
                    <span class="text-[11px] text-gray-500">
                      耗时 {{ result.elapsed_ms }} ms ·
                      <template v-if="result.request">{{ result.request.method }} {{ result.request.url }}</template>
                      <template v-else>{{ method }} {{ urlTemplate }}</template>
                    </span>
                  </div>
                  <p class="px-3 py-2 text-xs leading-relaxed" :class="result.ok ? 'text-green-800' : 'text-amber-800'">
                    <span v-if="result.ok">地址通了，接口也正常返回。</span>
                    <span v-else-if="result.error_kind === 'http_error'">
                      地址是通的，但接口拒绝了这次请求——先看状态码（401/403 多为凭据或权限，404 多为地址或路径写错，400 多为参数不对）。
                    </span>
                    <span v-else-if="result.error_kind === 'timeout'">
                      根本没连上目标接口：请求在 30 秒内没有响应。确认地址、端口与网络可达性。
                    </span>
                    <span v-else-if="result.error_kind === 'missing_param'">
                      {{ result.message }}
                    </span>
                    <span v-else-if="result.error_kind === 'unsupported_method'">
                      {{ result.message }}
                    </span>
                    <span v-else-if="result.error_kind === 'missing_user_variable'">
                      {{ result.message }}
                    </span>
                    <span v-else-if="result.error_kind === 'invalid_parameter_schema'">
                      {{ result.message }}
                    </span>
                    <span v-else-if="result.error_kind === 'invalid_signing_config'">
                      {{ result.message }}
                    </span>
                    <span v-else>
                      根本没连上目标接口（DNS 解析失败、连接被拒或证书问题）。这类问题与「接口拒绝请求」的排查方向不同。
                    </span>
                  </p>
                  <p v-if="result.message && !result.ok && result.error_kind !== 'missing_param' && result.error_kind !== 'unsupported_method' && result.error_kind !== 'missing_user_variable' && result.error_kind !== 'invalid_parameter_schema' && result.error_kind !== 'invalid_signing_config'" class="px-3 pb-2 text-[11px] text-gray-500">
                    {{ result.message }}
                  </p>

                  <details v-if="result.request" open class="border-t border-gray-100 bg-slate-50 px-3 py-2">
                    <summary class="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-[11px] font-medium text-gray-700">
                      <span>实际发出的请求（含系统级参数）</span>
                      <CopyButton v-if="curlCommand" :text="curlCommand" label="cURL" />
                    </summary>
                    <div class="mt-2 space-y-2">
                      <div class="flex items-start gap-2 rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-800 ring-1 ring-gray-100">
                        <div class="min-w-0 break-all">
                          <span class="font-bold text-primary">{{ result.request.method }}</span>
                          {{ result.request.url }}
                        </div>
                        <CopyButton :text="result.request.url" label="最终 URL" />
                      </div>
                      <div v-if="hasKeys(result.request.query)">
                        <div class="flex items-center gap-2">
                          <p class="text-[11px] font-medium text-gray-600">Query 参数</p>
                          <CopyButton :text="prettyJson(result.request.query)" label="Query 参数" />
                        </div>
                        <pre class="mt-1 max-h-36 overflow-auto rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">{{ prettyJson(result.request.query) }}</pre>
                      </div>
                      <div v-if="result.request.body !== null && result.request.body !== undefined">
                        <div class="flex items-center gap-2">
                          <p class="text-[11px] font-medium text-gray-600">请求体</p>
                          <CopyButton :text="prettyJson(result.request.body)" label="请求体" />
                        </div>
                        <pre class="mt-1 max-h-36 overflow-auto rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">{{ prettyJson(result.request.body) }}</pre>
                      </div>
                      <div v-if="signingHeaderEntries.length">
                        <div class="flex items-center gap-2">
                          <p class="text-[11px] font-medium text-gray-600">签名 Header（已脱敏）</p>
                          <CopyButton
                            :text="JSON.stringify(Object.fromEntries(signingHeaderEntries), null, 2)"
                            label="签名 Header"
                          />
                        </div>
                        <pre class="mt-1 max-h-36 overflow-auto rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">{{ prettyJson(Object.fromEntries(signingHeaderEntries)) }}</pre>
                      </div>
                      <div v-if="hasKeys(result.request.headers)">
                        <div class="flex items-center gap-2">
                          <p class="text-[11px] font-medium text-gray-600">Headers（已脱敏）</p>
                          <CopyButton :text="prettyJson(result.request.headers)" label="Headers" />
                        </div>
                        <pre class="mt-1 max-h-36 overflow-auto rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">{{ prettyJson(result.request.headers) }}</pre>
                      </div>
                      <p class="text-[10px] leading-relaxed text-gray-400">
                        系统级参数由平台按当前登录用户注入；这里展示的是请求内核最终实际发送的内容，敏感 Header 已脱敏。
                      </p>
                    </div>
                  </details>

                  <details v-if="hasKeys(result.response_headers)" class="border-t border-gray-100 bg-slate-50 px-3 py-2">
                    <summary class="cursor-pointer text-[11px] font-medium text-gray-700">响应 Headers（已脱敏）</summary>
                    <div class="mt-2">
                      <div class="flex items-center gap-2">
                        <p class="text-[11px] text-gray-500">目标接口返回的响应头</p>
                        <CopyButton :text="prettyJson(result.response_headers)" label="响应 Headers" />
                      </div>
                      <pre class="mt-1 max-h-36 overflow-auto rounded bg-white px-2 py-1.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">{{ prettyJson(result.response_headers) }}</pre>
                    </div>
                  </details>

                  <div v-if="result.body" class="bg-slate-900">
                    <div class="flex flex-wrap items-center gap-2 border-b border-slate-700 px-3 py-1.5">
                      <span class="text-[11px] font-medium text-slate-300">响应体</span>
                      <div v-if="responseBodyIsJson" class="flex rounded bg-slate-800 p-0.5 text-[10px] ring-1 ring-slate-700">
                        <button
                          type="button"
                          class="rounded px-1.5 py-0.5 font-medium"
                          :class="bodyView === 'pretty' ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-slate-200'"
                          @click="bodyView = 'pretty'"
                        >美化</button>
                        <button
                          type="button"
                          class="rounded px-1.5 py-0.5 font-medium"
                          :class="bodyView === 'raw' ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-slate-200'"
                          @click="bodyView = 'raw'"
                        >原文</button>
                      </div>
                      <CopyButton :text="result.body" label="响应体" dark />
                    </div>
                    <pre class="max-h-56 overflow-auto px-3 py-2.5 text-[11px] leading-relaxed text-slate-200">{{ responseBody }}</pre>
                  </div>
                  <p v-if="result.body_truncated" class="border-t border-gray-100 bg-gray-50 px-3 py-1.5 text-[11px] text-gray-500">
                    响应过大，仅显示前 2000 字符。
                  </p>
                </div>

                <ul class="space-y-1 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2.5">
                  <li class="text-[11px] leading-relaxed text-gray-500">
                    · 试跑由平台服务器发起，走的是与智能体运行时完全相同的请求逻辑（同一个方法和参数落位规则）。
                  </li>
                  <li class="text-[11px] leading-relaxed text-gray-500">
                    · 失败会分清是「接口通了但拒绝」还是「根本连不上」——这两种情况该查的东西不一样。
                  </li>
                  <li class="text-[11px] leading-relaxed text-gray-500">
                    · 测试值不落库；每次试跑都会记审计日志。目标地址若是内网，这里会提示但不会阻止你试跑。
                  </li>
                  <li class="text-[11px] leading-relaxed text-gray-500">
                    · 参数值里的 <code class="font-mono text-gray-600">{user.user_name}</code> 等用户变量会按当前登录用户替换；取不到值时会明确提示，不会发空值。
                  </li>
                </ul>
              </div>

              <div class="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-white px-4 py-3">
                <p class="text-[11px] text-gray-400">每次试跑都会记审计日志</p>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="rounded-md border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
                    @click="closeDrawer"
                  >关闭</button>
                  <button
                    type="button"
                    :disabled="loading"
                    class="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
                    @click="runPreview"
                  >
                    <PlayIcon class="h-3.5 w-3.5" />
                    {{ loading ? '试跑中...' : '试跑一次' }}
                  </button>
                </div>
              </div>
            </aside>
          </transition>
        </div>
      </transition>
    </teleport>
  </div>
</template>
