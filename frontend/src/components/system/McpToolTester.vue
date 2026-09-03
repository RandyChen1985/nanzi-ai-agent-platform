<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import axios from '@/utils/axios'
import { copyToClipboard } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import { renderSafeMarkdownPreview } from '@/utils/safeMarkdown'
import hljs from 'highlight.js'
import {
  PlayIcon,
  XMarkIcon,
  BeakerIcon,
  DocumentDuplicateIcon,
  CheckIcon,
} from '@heroicons/vue/24/outline'

type ResultKind = 'json' | 'markdown' | 'text'
type McpAuthStatus = {
  user_assertion_sent: boolean
  header: string | null
  value_masked: string | null
  audience: string | null
  issuer: string | null
  key_id: string | null
}

const props = defineProps<{
  tool: any,
  isOpen: boolean
}>()

const emit = defineEmits(['close'])
const { showToast } = useToast()

const loading = ref(false)
const result = ref<unknown>(null)
const error = ref<string | null>(null)
const args = ref<Record<string, any>>({})
const rawJsonArgs = ref<Record<string, string>>({})
const activeTab = ref<'input' | 'details'>('input')
const requestPayload = ref<{ arguments: Record<string, any> } | null>(null)
const requestCopied = ref(false)
const copied = ref(false)
const mcpAuth = ref<McpAuthStatus | null>(null)

const schema = computed(() => {
  try {
    return JSON.parse(props.tool.parameter_schema)
  } catch {
    return {}
  }
})

const properties = computed(() => schema.value.properties || {})
const requiredFields = computed(() => schema.value.required || [])

const getScalarType = (prop: any): string => {
  if (!prop) return ''
  if (typeof prop.type === 'string') return prop.type
  if (Array.isArray(prop.type)) return prop.type.find((type: unknown) => type !== 'null') || ''
  return ''
}

const isComplexType = (prop: any): boolean => {
  if (!prop) return false
  const t = prop.type
  if (t === 'object' || t === 'array') return true
  if (Array.isArray(t) && (t.includes('object') || t.includes('array'))) return true
  if (!t && (prop.properties || prop.items)) return true
  return false
}

const getPlaceholder = (prop: any): string => {
  if (prop.type === 'array' || prop.items) {
    return 'JSON 数组格式，例如: ["item1", "item2"]'
  }
  if (prop.type === 'object' || prop.properties) {
    return 'JSON 对象格式，例如: {"key": "value"}'
  }
  return prop.description || '请输入 JSON 内容'
}

watch([() => props.tool, () => props.isOpen], () => {
  args.value = {}
  rawJsonArgs.value = {}
  result.value = null
  error.value = null
  activeTab.value = 'input'
  requestPayload.value = null
  requestCopied.value = false
  copied.value = false
  mcpAuth.value = null
}, { immediate: true })

const normalizeResultText = (value: unknown): string => {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const tryPrettyJson = (text: string): string | null => {
  const trimmed = text.trim()
  if (!trimmed || (trimmed[0] !== '{' && trimmed[0] !== '[')) return null
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return null
  }
}

const looksLikeMarkdown = (text: string): boolean => {
  const sample = text.trim()
  if (!sample || sample.length < 8) return false
  return /^(#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s)/m.test(sample)
    || /```[\s\S]*```/.test(sample)
    || /\*\*[^*\n]+\*\*/.test(sample)
    || /^\|.+\|$/m.test(sample)
    || /^>\s+/m.test(sample)
}

const formattedResult = computed(() => {
  const source = error.value != null ? error.value : result.value
  if (source == null || source === '') return null

  const rawText = normalizeResultText(source)
  const prettyJson = tryPrettyJson(rawText)
  if (prettyJson != null) {
    let highlighted = ''
    try {
      highlighted = hljs.highlight(prettyJson, { language: 'json', ignoreIllegals: true }).value
    } catch {
      highlighted = ''
    }
    return {
      kind: 'json' as ResultKind,
      copyText: prettyJson,
      displayText: prettyJson,
      html: highlighted,
      label: 'JSON',
    }
  }

  if (!error.value && looksLikeMarkdown(rawText)) {
    return {
      kind: 'markdown' as ResultKind,
      copyText: rawText,
      displayText: rawText,
      html: renderSafeMarkdownPreview(rawText),
      label: 'Markdown',
    }
  }

  return {
    kind: 'text' as ResultKind,
    copyText: rawText,
    displayText: rawText,
    html: '',
    label: 'Text',
  }
})

const formattedRequest = computed(() => {
  if (!requestPayload.value) return null
  return JSON.stringify(requestPayload.value, null, 2)
})

const handleCopyRequest = async () => {
  if (!formattedRequest.value) return
  const ok = await copyToClipboard(formattedRequest.value)
  if (!ok) {
    showToast('复制失败', 'error')
    return
  }
  requestCopied.value = true
  showToast('已复制到剪贴板', 'success')
  window.setTimeout(() => {
    requestCopied.value = false
  }, 1500)
}

const handleCopyResult = async () => {
  const payload = formattedResult.value
  if (!payload?.copyText) return
  const ok = await copyToClipboard(payload.copyText)
  if (!ok) {
    showToast('复制失败', 'error')
    return
  }
  copied.value = true
  showToast('已复制到剪贴板', 'success')
  window.setTimeout(() => {
    copied.value = false
  }, 1500)
}

const executeTool = async () => {
  loading.value = true
  result.value = null
  error.value = null

  // 整理参数，解析复杂类型的 JSON
  const finalArgs: Record<string, any> = {}
  for (const [key, prop] of Object.entries(properties.value as Record<string, any>)) {
    if (isComplexType(prop)) {
      const raw = (rawJsonArgs.value[key] ?? '').trim()
      if (raw) {
        try {
          finalArgs[key] = JSON.parse(raw)
        } catch (err: any) {
          showToast(`参数 ${key} 的 JSON 格式错误: ${err.message}`, 'error')
          loading.value = false
          return
        }
      } else if (requiredFields.value.includes(key)) {
        showToast(`必填参数 ${key} 不能为空`, 'error')
        loading.value = false
        return
      }
    } else if (args.value[key] !== undefined && args.value[key] !== '') {
      finalArgs[key] = args.value[key]
    } else if (requiredFields.value.includes(key)) {
      showToast(`必填参数 ${key} 不能为空`, 'error')
      loading.value = false
      return
    }
  }

  requestPayload.value = {
    arguments: JSON.parse(JSON.stringify(finalArgs))
  }
  requestCopied.value = false
  copied.value = false
  mcpAuth.value = null

  try {
    const res = await axios.post(`/api/portal/mcp/tools/${props.tool.id}/execute`, {
      arguments: finalArgs
    })

    mcpAuth.value = res.data.mcp_auth || null
    if (res.data.status === 'success') {
      result.value = res.data.result
    } else {
      error.value = res.data.message || '执行失败'
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
    activeTab.value = 'details'
  }
}
</script>

<template>
  <div v-if="isOpen" class="fixed inset-0 z-[70] flex justify-end">
    <!-- Backdrop -->
    <div class="absolute inset-0 bg-gray-900/20 backdrop-blur-sm transition-opacity" @click="emit('close')"></div>

    <!-- Drawer -->
    <div class="relative w-full max-w-md bg-white h-full shadow-2xl flex flex-col animate-slide-in-right">
      <div class="p-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
        <h3 class="text-sm font-bold text-gray-800 flex items-center">
          <BeakerIcon class="w-4 h-4 mr-2 text-primary" />
          工具测试台
        </h3>
        <button @click="emit('close')" class="p-1 text-gray-400 hover:text-gray-600 rounded-md">
          <XMarkIcon class="w-5 h-5" />
        </button>
      </div>

      <div class="p-6 border-b border-gray-100">
        <h2 class="text-lg font-bold text-gray-900 mb-1">{{ tool.tool_name }}</h2>
        <p class="text-xs text-gray-500 italic">{{ tool.tool_description || '暂无描述' }}</p>
      </div>

      <div class="px-6 pt-4 border-b border-gray-100">
        <div class="flex gap-5" role="tablist" aria-label="工具测试内容">
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'input'"
            class="pb-3 text-sm font-semibold border-b-2 transition-colors"
            :class="activeTab === 'input' ? 'text-primary border-primary' : 'text-gray-400 border-transparent hover:text-gray-600'"
            @click="activeTab = 'input'"
          >参数输入</button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'details'"
            class="pb-3 text-sm font-semibold border-b-2 transition-colors"
            :class="activeTab === 'details' ? 'text-primary border-primary' : 'text-gray-400 border-transparent hover:text-gray-600'"
            @click="activeTab = 'details'"
          >调用详情</button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
        <!-- Input Form -->
        <div v-if="activeTab === 'input'" class="space-y-4">
          <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider">参数输入</h4>
          <div v-if="Object.keys(properties).length === 0" class="text-xs text-gray-400 italic">
            此工具无需参数
          </div>
          <div v-else class="space-y-3">
            <div v-for="(prop, key) in properties" :key="key">
              <label class="block text-xs font-medium text-gray-700 mb-1">
                {{ key }} <span v-if="requiredFields.includes(key)" class="text-red-500">*</span>
              </label>
              <input
                v-if="getScalarType(prop) === 'string' || (!prop.type && !isComplexType(prop))"
                v-model="args[key]"
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                :placeholder="prop.description"
              />
              <input
                v-else-if="getScalarType(prop) === 'integer' || getScalarType(prop) === 'number'"
                type="number"
                v-model.number="args[key]"
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none"
                :placeholder="prop.description"
              />
              <label v-else-if="getScalarType(prop) === 'boolean'" class="flex items-center space-x-2 cursor-pointer">
                <input type="checkbox" v-model="args[key]" class="w-4 h-4 text-primary border-gray-300 rounded focus:ring-primary" />
                <span class="text-xs text-gray-500">{{ prop.description || '启用' }}</span>
              </label>
              <div v-else-if="isComplexType(prop)">
                <textarea
                  v-model="rawJsonArgs[key]"
                  rows="3"
                  class="w-full px-3 py-2 text-xs font-mono border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                  :placeholder="getPlaceholder(prop)"
                ></textarea>
              </div>
              <p v-if="prop.description" class="text-[10px] text-gray-400 mt-1">{{ prop.description }}</p>
            </div>
          </div>
        </div>

        <!-- Call Details -->
        <div v-else class="space-y-6">
          <div v-if="!requestPayload" class="rounded-lg border border-dashed border-gray-200 bg-gray-50 p-6 text-center text-xs text-gray-400">
            运行测试后查看本次调用详情
          </div>

          <!-- MCP 认证状态：只展示脱敏结果，不把可重放的 JWT 返回浏览器 -->
          <div v-if="mcpAuth" class="space-y-2 rounded-lg border border-indigo-100 bg-indigo-50/60 p-3">
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-xs font-bold text-indigo-900">本次调用认证信息</h4>
            <span
              class="rounded px-1.5 py-0.5 text-[10px] font-semibold"
              :class="mcpAuth.user_assertion_sent ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'"
            >{{ mcpAuth.user_assertion_sent ? '用户身份已发送' : '未发送用户身份' }}</span>
          </div>
          <div v-if="mcpAuth.user_assertion_sent" class="space-y-1 text-[10px] leading-relaxed text-indigo-900">
            <div class="break-all rounded border border-indigo-100 bg-white/80 px-2 py-1.5 font-mono">
              {{ mcpAuth.header || 'X-Nanzi-User-Assertion' }}: {{ mcpAuth.value_masked || '********' }}
            </div>
            <div class="grid grid-cols-1 gap-1 sm:grid-cols-3">
              <span><b>Audience：</b>{{ mcpAuth.audience || '-' }}</span>
              <span><b>Issuer：</b>{{ mcpAuth.issuer || '-' }}</span>
              <span><b>Key ID：</b>{{ mcpAuth.key_id || '-' }}</span>
            </div>
            <p class="text-indigo-700">完整签名值不会展示；业务 MCP 收到完整 Header 后自行验签。</p>
          </div>
          <p v-else class="text-[10px] leading-relaxed text-gray-500">当前 MCP 未开启用户身份传递，本次测试只使用原有认证 Header。</p>
          </div>

          <div v-if="formattedRequest" class="space-y-2">
            <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider">请求参数</h4>
            <div class="group/request relative rounded-lg border border-slate-800 bg-slate-900 overflow-hidden">
              <button
                type="button"
                class="absolute top-2 right-2 z-10 inline-flex items-center justify-center rounded-md border border-slate-700 bg-slate-800/90 p-1.5 text-slate-300 shadow-sm opacity-0 transition-all group-hover/request:opacity-100 focus:opacity-100 hover:bg-slate-700 hover:text-white"
                title="复制请求参数"
                aria-label="复制请求参数"
                @click="handleCopyRequest"
              >
                <CheckIcon v-if="requestCopied" class="w-3.5 h-3.5 text-emerald-400" />
                <DocumentDuplicateIcon v-else class="w-3.5 h-3.5" />
              </button>
              <pre class="p-3 pr-10 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words max-h-[260px] overflow-y-auto custom-scrollbar m-0 text-slate-100">{{ formattedRequest }}</pre>
            </div>
          </div>

          <!-- Result Area -->
          <div v-if="formattedResult" class="space-y-2 animate-fade-in">

          <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
            响应结果
            <span
              v-if="error"
              class="text-[10px] text-red-500 bg-red-50 px-1.5 py-0.5 rounded normal-case tracking-normal"
            >Failed</span>
            <span
              v-else
              class="text-[10px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded normal-case tracking-normal"
            >Success</span>
            <span
              class="text-[10px] px-1.5 py-0.5 rounded normal-case tracking-normal font-semibold"
              :class="error
                ? 'bg-red-50 text-red-500'
                : formattedResult.kind === 'json'
                  ? 'bg-emerald-50 text-emerald-700'
                  : formattedResult.kind === 'markdown'
                    ? 'bg-indigo-50 text-indigo-700'
                    : 'bg-slate-100 text-slate-600'"
            >{{ formattedResult.label }}</span>
          </h4>

          <div
            class="group/result relative rounded-lg border overflow-hidden"
            :class="error
              ? 'bg-red-50 border-red-100'
              : formattedResult.kind === 'markdown'
                ? 'bg-white border-gray-200'
                : 'bg-slate-900 border-slate-800'"
          >
            <button
              type="button"
              class="absolute top-2 right-2 z-10 inline-flex items-center justify-center rounded-md border p-1.5 shadow-sm transition-all opacity-0 group-hover/result:opacity-100 focus:opacity-100"
              :class="error || formattedResult.kind === 'markdown'
                ? 'bg-white/95 border-gray-200 text-gray-500 hover:text-primary hover:bg-gray-50'
                : 'bg-slate-800/90 border-slate-700 text-slate-300 hover:text-white hover:bg-slate-700'"
              title="复制结果"
              aria-label="复制结果"
              @click="handleCopyResult"
            >
              <CheckIcon v-if="copied" class="w-3.5 h-3.5 text-emerald-500" />
              <DocumentDuplicateIcon v-else class="w-3.5 h-3.5" />
            </button>

            <div
              v-if="formattedResult.kind === 'json' && formattedResult.html"
              class="mcp-result-json p-3 pr-10 text-xs font-mono leading-relaxed whitespace-pre overflow-x-auto max-h-[360px] overflow-y-auto custom-scrollbar"
              :class="error ? 'text-red-700' : 'text-slate-100'"
              v-html="formattedResult.html"
            />
            <div
              v-else-if="formattedResult.kind === 'markdown'"
              class="mcp-result-markdown p-3 pr-10 text-sm text-gray-800 leading-relaxed max-h-[360px] overflow-y-auto custom-scrollbar"
              v-html="formattedResult.html"
            />
            <pre
              v-else
              class="p-3 pr-10 text-xs font-mono whitespace-pre-wrap break-words max-h-[360px] overflow-y-auto custom-scrollbar m-0"
              :class="error ? 'text-red-700' : 'text-green-400'"
            >{{ formattedResult.displayText }}</pre>
          </div>
          </div>
        </div>
      </div>

      <div class="p-4 border-t border-gray-100 bg-gray-50 flex justify-end">
        <button
          @click="executeTool"
          :disabled="loading"
          class="w-full px-4 py-2 bg-primary text-white rounded-lg shadow-lg shadow-primary/20 hover:bg-primary-dark transition-all flex justify-center items-center font-bold text-sm disabled:opacity-70 disabled:cursor-not-allowed"
        >
          <svg v-if="loading" class="animate-spin h-4 w-4 mr-2 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <PlayIcon v-else class="w-4 h-4 mr-2" />
          运行测试
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-slide-in-right { animation: slideInRight 0.3s ease-out; }
@keyframes slideInRight { from { transform: translateX(100%); } to { transform: translateX(0); } }

.mcp-result-json :deep(.hljs-attr) { color: #7dd3fc; }
.mcp-result-json :deep(.hljs-string) { color: #86efac; }
.mcp-result-json :deep(.hljs-number) { color: #fcd34d; }
.mcp-result-json :deep(.hljs-literal) { color: #f9a8d4; }
.mcp-result-json :deep(.hljs-punctuation) { color: #cbd5e1; }

.mcp-result-markdown :deep(p) { margin: 0 0 0.65em; }
.mcp-result-markdown :deep(p:last-child) { margin-bottom: 0; }
.mcp-result-markdown :deep(ul),
.mcp-result-markdown :deep(ol) { margin: 0.4em 0 0.65em; padding-left: 1.25rem; }
.mcp-result-markdown :deep(li) { margin: 0.15em 0; }
.mcp-result-markdown :deep(code) {
  font-size: 0.75rem;
  background: #f3f4f6;
  border-radius: 0.25rem;
  padding: 0.1rem 0.3rem;
}
.mcp-result-markdown :deep(pre) {
  margin: 0.5em 0;
  padding: 0.75rem;
  border-radius: 0.5rem;
  background: #0f172a;
  color: #e2e8f0;
  overflow-x: auto;
}
.mcp-result-markdown :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}
.mcp-result-markdown :deep(a) { color: #2563eb; text-decoration: underline; }
.mcp-result-markdown :deep(h1),
.mcp-result-markdown :deep(h2),
.mcp-result-markdown :deep(h3),
.mcp-result-markdown :deep(h4) {
  font-weight: 700;
  margin: 0.75em 0 0.4em;
  line-height: 1.3;
}
.mcp-result-markdown :deep(blockquote) {
  margin: 0.5em 0;
  padding-left: 0.75rem;
  border-left: 3px solid #cbd5e1;
  color: #64748b;
}
.mcp-result-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5em 0;
  font-size: 0.75rem;
}
.mcp-result-markdown :deep(th),
.mcp-result-markdown :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 0.35rem 0.5rem;
  text-align: left;
}
.mcp-result-markdown :deep(th) { background: #f9fafb; font-weight: 600; }
</style>
