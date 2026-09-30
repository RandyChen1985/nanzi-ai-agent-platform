<script setup lang="ts">
/**
 * Langfuse LLM 链路追踪配置。
 *
 * 独立于「参数配置」的键值表格：连接参数强类型、密钥加密存储且不回显明文，
 * 保存后立即生效（服务端会刷新进程内快照并重建 client）。
 */
import { computed, onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '../../composables/useToast'
import { useUser } from '../../composables/useUser'
import { ArrowTopRightOnSquareIcon } from '@heroicons/vue/24/outline'

const ENDPOINT = '/api/portal/system/langfuse'

const { showToast } = useToast()
const { hasPermission } = useUser()
const canSave = hasPermission('element:system:langfuse_save')

interface LangfuseConfigView {
  enabled: boolean
  host: string | null
  public_key: string | null
  has_secret_key: boolean
  sample_rate: number | string
  capture_content: boolean
  environment: string | null
  release: string | null
  timeout_seconds: number
  trace_url_template: string | null
  updated_by: string | null
  status: string
  status_label: string
  status_detail: string | null
  client_active: boolean
}

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ success: boolean; message: string } | null>(null)

/** 密钥输入框留空表示「保持不变」，只在用户真正输入时提交。 */
const secretKeyInput = ref('')

const form = ref({
  enabled: false,
  host: '',
  public_key: '',
  sample_rate: 1,
  capture_content: true,
  environment: '',
  release: '',
  timeout_seconds: 5,
  trace_url_template: '',
})

const meta = ref<{
  has_secret_key: boolean
  updated_by: string | null
  status: string
  status_label: string
  status_detail: string | null
  client_active: boolean
}>({
  has_secret_key: false,
  updated_by: null,
  status: 'disabled',
  status_label: '已关闭',
  status_detail: null,
  client_active: false,
})

const statusTone = computed(() => {
  if (meta.value.status === 'enabled' && meta.value.client_active) {
    return 'bg-green-50 text-green-700 border-green-200'
  }
  if (meta.value.status === 'degraded') {
    return 'bg-red-50 text-red-700 border-red-200'
  }
  if (meta.value.status === 'not_configured') {
    return 'bg-amber-50 text-amber-700 border-amber-200'
  }
  return 'bg-gray-50 text-gray-600 border-gray-200'
})

const applyView = (data: LangfuseConfigView) => {
  form.value = {
    enabled: Boolean(data.enabled),
    host: data.host || '',
    public_key: data.public_key || '',
    sample_rate: Number(data.sample_rate ?? 1),
    capture_content: data.capture_content !== false,
    environment: data.environment || '',
    release: data.release || '',
    timeout_seconds: Number(data.timeout_seconds ?? 5),
    trace_url_template: data.trace_url_template || '',
  }
  meta.value = {
    has_secret_key: Boolean(data.has_secret_key),
    updated_by: data.updated_by ?? null,
    status: data.status || 'disabled',
    status_label: data.status_label || '-',
    status_detail: data.status_detail ?? null,
    client_active: Boolean(data.client_active),
  }
  secretKeyInput.value = ''
}

const fetchConfig = async () => {
  loading.value = true
  try {
    const response = await axios.get(ENDPOINT)
    applyView(response.data as LangfuseConfigView)
  } catch (error) {
    console.error('加载 Langfuse 配置失败', error)
    showToast({ type: 'error', message: '加载 Langfuse 配置失败' })
  } finally {
    loading.value = false
  }
}

const buildProbePayload = () => {
  // 探测用的是「当前表单值」（含未保存的修改），未填的字段由服务端回落到已保存配置。
  const payload: Record<string, unknown> = {
    enabled: form.value.enabled,
    host: form.value.host.trim(),
    public_key: form.value.public_key.trim(),
    timeout_seconds: form.value.timeout_seconds,
  }
  // 密钥留空（＝不修改）时不提交，服务端会复用已保存的密钥。
  const typedSecret = secretKeyInput.value.trim()
  if (typedSecret) {
    payload.secret_key = typedSecret
  }
  return payload
}

const buildPayload = () => {
  // 关闭态配置区不可见，只提交开关本身，避免把隐藏字段的旧值（甚至空值）一并写回。
  if (!form.value.enabled) {
    return { enabled: false }
  }
  const payload: Record<string, unknown> = {
    enabled: form.value.enabled,
    host: form.value.host.trim(),
    public_key: form.value.public_key.trim(),
    sample_rate: form.value.sample_rate,
    capture_content: form.value.capture_content,
    environment: form.value.environment.trim(),
    release: form.value.release.trim(),
    timeout_seconds: form.value.timeout_seconds,
    trace_url_template: form.value.trace_url_template.trim(),
  }
  // 只有用户真的输入了新密钥才提交；留空 = 不改动（空字符串语义是「清空」）。
  const typedSecret = secretKeyInput.value.trim()
  if (typedSecret) {
    payload.secret_key = typedSecret
  }
  return payload
}

const save = async () => {
  saving.value = true
  testResult.value = null
  try {
    const response = await axios.put(ENDPOINT, buildPayload())
    const body = response.data as { data?: LangfuseConfigView }
    if (body?.data) {
      applyView(body.data)
    } else {
      await fetchConfig()
    }
    showToast({ type: 'success', message: 'Langfuse 配置已保存' })
  } catch (error: any) {
    const message = error?.response?.data?.message || '保存失败，请检查配置值'
    showToast({ type: 'error', message })
  } finally {
    saving.value = false
  }
}

const testConnection = async () => {
  testing.value = true
  testResult.value = null
  try {
    const response = await axios.post(`${ENDPOINT}/test`, buildProbePayload())
    testResult.value = response.data as { success: boolean; message: string }
    showToast({
      type: testResult.value.success ? 'success' : 'error',
      message: testResult.value.message,
    })
  } catch (error: any) {
    const message = error?.response?.data?.message || '连通性探测失败'
    testResult.value = { success: false, message }
    showToast({ type: 'error', message })
  } finally {
    testing.value = false
  }
}

const clearSecret = async () => {
  saving.value = true
  try {
    // 空字符串在服务端语义为「清空已保存的密钥」。
    const response = await axios.put(ENDPOINT, { secret_key: '' })
    const body = response.data as { data?: LangfuseConfigView }
    if (body?.data) applyView(body.data)
    showToast({ type: 'success', message: '已清除密钥' })
  } catch (error: any) {
    showToast({ type: 'error', message: error?.response?.data?.message || '清除密钥失败' })
  } finally {
    saving.value = false
  }
}

const openSelfHostedHint = () => {
  window.open('https://langfuse.com/self-hosting', '_blank', 'noopener')
}

onMounted(fetchConfig)
</script>

<template>
  <div class="space-y-6 h-full overflow-y-auto pb-12 custom-scrollbar">
    <div class="bg-white rounded-lg border border-gray-200 p-6">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 class="text-lg font-semibold text-gray-900">链路追踪</h3>
          <p class="mt-1 text-sm text-gray-500">
            把智能体整轮对话（路由、模型调用、工具调用）上报到 Langfuse，用于排障与性能分析。
            平台只做客户端接入，可对接任意 Langfuse 实例。
          </p>
        </div>
        <button
          type="button"
          class="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          @click="openSelfHostedHint"
        >
          部署说明
          <ArrowTopRightOnSquareIcon class="w-4 h-4" />
        </button>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <span
          class="inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium"
          :class="statusTone"
        >
          {{ meta.status_label }}
        </span>
        <span class="text-xs text-gray-500">
          进程内 client：{{ meta.client_active ? '已就绪' : '未建立' }}
        </span>
        <span v-if="meta.updated_by" class="text-xs text-gray-400">
          最后修改人：{{ meta.updated_by }}
        </span>
      </div>
      <p v-if="meta.status_detail" class="mt-2 text-xs text-red-600">
        最近错误：{{ meta.status_detail }}
      </p>
    </div>

    <div class="bg-white rounded-lg border border-gray-200 p-6 space-y-5">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="text-sm font-medium text-gray-900">总开关</div>
          <p class="mt-1 text-xs text-gray-500">
            关闭后不创建任何 span，对性能无影响；开启后按下面的采样率上报。
          </p>
        </div>
        <label class="inline-flex items-center cursor-pointer">
          <input
            v-model="form.enabled"
            type="checkbox"
            class="sr-only peer"
            :disabled="!canSave"
          />
          <span
            class="w-11 h-6 bg-gray-200 peer-checked:bg-primary rounded-full transition-colors relative
                   after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white
                   after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-5"
          />
        </label>
      </div>

      <!-- 关闭态只留开关本身：一堆用不上的连接参数会让「已关闭」显得自相矛盾 -->
      <div
        v-if="!form.enabled"
        class="rounded-lg bg-gray-50 border border-dashed border-gray-200 px-4 py-3"
      >
        <p class="text-xs text-gray-500">
          链路追踪当前已关闭。开启总开关后，这里会显示 Langfuse 连接参数、采样率与内容上报选项。
        </p>
      </div>

      <template v-else>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-gray-700">Langfuse 地址</label>
          <input
            v-model="form.host"
            type="text"
            placeholder="http://langfuse:3000"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
          <p class="mt-1 text-xs text-gray-400">必须带 http:// 或 https:// 前缀</p>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">Public Key</label>
          <input
            v-model="form.public_key"
            type="text"
            placeholder="pk-lf-..."
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
        </div>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700">Secret Key</label>
        <div class="mt-1 flex flex-wrap items-center gap-3">
          <input
            v-model="secretKeyInput"
            type="password"
            autocomplete="new-password"
            :placeholder="meta.has_secret_key ? '已保存（留空表示不修改）' : 'sk-lf-...'"
            class="flex-1 min-w-[16rem] rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
          <span
            class="text-xs"
            :class="meta.has_secret_key ? 'text-green-600' : 'text-gray-400'"
          >
            {{ meta.has_secret_key ? '密钥已配置（加密存储，不回显）' : '尚未配置密钥' }}
          </span>
          <button
            v-if="canSave && meta.has_secret_key"
            type="button"
            class="text-xs text-red-600 hover:underline"
            :disabled="saving"
            @click="clearSecret"
          >
            清除密钥
          </button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label class="block text-sm font-medium text-gray-700">采样率</label>
          <input
            v-model.number="form.sample_rate"
            type="number"
            min="0"
            max="1"
            step="0.05"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
          <p class="mt-1 text-xs text-gray-400">按整轮对话采样，0~1</p>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">超时（秒）</label>
          <input
            v-model.number="form.timeout_seconds"
            type="number"
            min="1"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">环境标识</label>
          <input
            v-model="form.environment"
            type="text"
            placeholder="prod / staging"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium text-gray-700">发布版本</label>
          <input
            v-model="form.release"
            type="text"
            placeholder="2026.09"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
          <p class="mt-1 text-xs text-gray-400">便于在 Langfuse 里按版本对比指标</p>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">跳转链接模板</label>
          <input
            v-model="form.trace_url_template"
            type="text"
            placeholder="http://langfuse:3000/trace/{trace_id}"
            class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            :disabled="!canSave"
          />
          <p class="mt-1 text-xs text-gray-400">用 {trace_id} 占位，供对话日志跳转</p>
        </div>
      </div>

      <div class="flex items-start justify-between gap-4 border-t border-gray-100 pt-4">
        <div>
          <div class="text-sm font-medium text-gray-900">上报提示词与回复内容</div>
          <p class="mt-1 text-xs text-gray-500">
            关闭后只上报耗时、token 用量、模型与工具名等元数据，不包含对话正文。
          </p>
        </div>
        <label class="inline-flex items-center cursor-pointer">
          <input
            v-model="form.capture_content"
            type="checkbox"
            class="sr-only peer"
            :disabled="!canSave"
          />
          <span
            class="w-11 h-6 bg-gray-200 peer-checked:bg-primary rounded-full transition-colors relative
                   after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white
                   after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-5"
          />
        </label>
      </div>
      </template>

      <div class="flex flex-wrap items-center gap-3 border-t border-gray-100 pt-4">
        <button
          type="button"
          class="px-4 py-2 bg-primary text-white text-sm rounded-md hover:bg-primary-dark transition-colors disabled:opacity-50"
          :disabled="!canSave || saving"
          @click="save"
        >
          {{ saving ? '保存中...' : '保存配置' }}
        </button>
        <button
          v-if="form.enabled"
          type="button"
          class="px-4 py-2 border border-gray-300 text-sm rounded-md text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
          :disabled="testing"
          @click="testConnection"
        >
          {{ testing ? '探测中...' : '测试连接' }}
        </button>
        <button
          type="button"
          class="px-4 py-2 text-sm rounded-md text-gray-600 hover:bg-gray-50 transition-colors"
          :disabled="loading"
          @click="fetchConfig"
        >
          重新加载
        </button>
        <span v-if="form.enabled" class="text-xs text-gray-400">
          测试连接使用当前表单值，未保存的修改也会生效
        </span>
        <span v-if="!canSave" class="text-xs text-amber-600">
          当前账号没有保存权限（element:system:langfuse_save）
        </span>
      </div>

      <p
        v-if="testResult"
        class="text-sm"
        :class="testResult.success ? 'text-green-600' : 'text-red-600'"
      >
        {{ testResult.message }}
      </p>
    </div>

    <div class="bg-gray-50 rounded-lg border border-gray-200 p-5 text-xs text-gray-500 space-y-1">
      <p>· 配置保存后立即生效，无需重启服务。</p>
      <p>· 密钥使用平台统一的加密方式存储，任何接口都不会返回密钥明文。</p>
      <p>· 追踪是旁路能力：Langfuse 不可用时只影响追踪数据，不影响对话本身。</p>
    </div>
  </div>
</template>
