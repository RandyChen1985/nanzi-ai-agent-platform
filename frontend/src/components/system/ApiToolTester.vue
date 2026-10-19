<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { toolApi, type SysApiToolPreviewResult } from '../../api/tool'
import { parseToolSchema } from '../../utils/toolSchema'
import { PlayIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  method: string
  urlTemplate: string
  headersStr: string
  schemaStr: string
}>()

const values = ref<Record<string, string>>({})
const loading = ref(false)
const result = ref<SysApiToolPreviewResult | null>(null)
const failure = ref<string>('')

const params = computed(() => parseToolSchema(props.schemaStr).params)

/** 私网 / 回环 / 链路本地：**只提示，不阻止**（与运行时同权的既定决策）。 */
const isPrivateTarget = computed(() => {
  const matched = props.urlTemplate.match(/^[a-zA-Z][a-zA-Z0-9+.\-]*:\/\/([^/?#]+)/)
  if (!matched) return false
  const authority = matched[1].split('@').pop() || ''
  const host = authority.split(':')[0].toLowerCase()
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
watch(() => [props.method, props.urlTemplate, props.headersStr, props.schemaStr], () => {
  const next: Record<string, string> = {}
  for (const param of params.value) {
    if (!param.required && param.defaultValue) next[param.name] = param.defaultValue
  }
  values.value = next
  result.value = null
  failure.value = ''
}, { immediate: true })

const missingRequired = computed(() => params.value
  .filter((param) => param.required && !String(values.value[param.name] ?? '').trim())
  .map((param) => param.name))

const runPreview = async () => {
  failure.value = ''
  result.value = null

  if (!props.urlTemplate.trim()) {
    failure.value = '还没有填请求地址'
    return
  }
  if (missingRequired.value.length > 0) {
    // 后端也会拦一次，但先在本地说清楚缺哪个，避免一次无谓往返
    failure.value = `请先填写必填参数：${missingRequired.value.join('、')}`
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
    })
    result.value = res.data
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
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h4 class="text-sm font-bold text-gray-800">试跑一次</h4>
        <p class="text-xs text-gray-500 mt-1">
          填入一组测试值，真正调一次看看通不通。<b>测试值不会保存到工具配置里。</b>
        </p>
      </div>
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

    <p v-if="failure" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
      {{ failure }}
    </p>

    <div v-if="result" class="overflow-hidden rounded-lg border" :class="result.ok ? 'border-green-200' : 'border-amber-200'">
      <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2" :class="result.ok ? 'border-green-100 bg-green-50' : 'border-amber-100 bg-amber-50'">
        <span
          class="rounded px-1.5 py-0.5 text-[11px] font-bold"
          :class="result.ok ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-800'"
        >{{ headline }}</span>
        <span class="text-[11px] text-gray-500">
          耗时 {{ result.elapsed_ms }} ms · {{ method }} {{ urlTemplate }}
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
        <span v-else>
          根本没连上目标接口（DNS 解析失败、连接被拒或证书问题）。这类问题与「接口拒绝请求」的排查方向不同。
        </span>
      </p>
      <p v-if="result.message && !result.ok && result.error_kind !== 'missing_param' && result.error_kind !== 'unsupported_method' && result.error_kind !== 'missing_user_variable'" class="px-3 pb-2 text-[11px] text-gray-500">
        {{ result.message }}
      </p>
      <pre
        v-if="result.body"
        class="max-h-56 overflow-auto bg-slate-900 px-3 py-2.5 text-[11px] leading-relaxed text-slate-200"
      >{{ result.body }}</pre>
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
</template>
