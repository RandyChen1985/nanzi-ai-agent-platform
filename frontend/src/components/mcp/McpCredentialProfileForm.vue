<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { credentialError, getCredentialVersion, isStructuredType, listCredentialTypes, type CredentialType } from '@/composables/useCredentialFields'
import {
  credentialProfileUrl, listMcpCredentialCandidates, splitMcpResultPaths, type CredentialCandidate, type McpCredentialConfig,
  type McpCredentialProfile, type SelectionMode,
} from '@/utils/mcpCredentialProfiles'

const props = defineProps<{ toolId: string; config: McpCredentialConfig }>()
const emit = defineEmits<{ saved: [McpCredentialProfile] }>()

const types = ref<CredentialType[]>([])
const current = ref<McpCredentialProfile | null>(props.config.profile)
const typeKey = ref(current.value?.type_key || '')
const typeVersion = ref(current.value?.type_version || 0)
const versionDefinition = ref<CredentialType | null>(null)
const selectionMode = ref<SelectionMode>(current.value?.selection_mode || 'infer')
const mappings = ref<Record<string, string>>({ ...current.value?.mappings })
const resultFieldText = ref(props.config.stale ? '' : current.value?.model_result_fields?.join('\n') ?? '')
const busy = ref(false)
const loadingType = ref(false)
const error = ref('')
const saved = ref('')
const fixedId = ref('')
const candidates = ref<CredentialCandidate[]>([])
const loadingCandidates = ref(false)
const candidateError = ref('')
let candidateRequest = 0
const fixedUnavailable = computed(() => !!fixedId.value && !candidates.value.some(item => item.id === fixedId.value))
const versionOptions = computed(() => {
  const max = Math.max(types.value.find(item => item.type_key === typeKey.value)?.latest_version || 0, typeVersion.value)
  return Array.from({ length: max }, (_, index) => index + 1).reverse()
})
const unmapped = computed(() => props.config.parameters.filter(arg => arg.group_binding_required && !mappings.value[arg.name]))
const mapped = computed(() => Object.entries(mappings.value).filter(([, field]) => field))
const canSave = computed(() => !!versionDefinition.value && !busy.value && !loadingType.value
  && !!mapped.value.length && !unmapped.value.length
  && (selectionMode.value !== 'fixed' || (!loadingCandidates.value && !candidateError.value && !fixedUnavailable.value)))

function fieldOptions(targetType: string | string[] | null) {
  return Object.entries(versionDefinition.value?.schema.properties || {}).filter(([, field]) =>
    field.type === targetType || (field.type === 'integer' && targetType === 'number'),
  )
}

async function loadCandidates() {
  const request = ++candidateRequest
  candidates.value = []
  fixedId.value = ''
  candidateError.value = ''
  loadingCandidates.value = false
  if (selectionMode.value !== 'fixed' || !typeKey.value || !typeVersion.value) return
  loadingCandidates.value = true
  try {
    const data = await listMcpCredentialCandidates(props.toolId, {
      type_key: typeKey.value, type_version: typeVersion.value,
    })
    if (request !== candidateRequest) return
    candidates.value = data.items
    fixedId.value = data.fixed_secret_id || ''
  } catch (err) {
    if (request === candidateRequest) candidateError.value = credentialError(err, '无法加载可用凭据，请重试')
  } finally {
    if (request === candidateRequest) loadingCandidates.value = false
  }
}

// 按正在编辑的类型查询；切换模式、类型或版本时丢弃迟到响应。
watch([() => props.toolId, typeKey, typeVersion, selectionMode], () => void loadCandidates(), { immediate: true })
onBeforeUnmount(() => { candidateRequest += 1 })

function changeType() {
  typeVersion.value = types.value.find(item => item.type_key === typeKey.value)?.latest_version || 0
  mappings.value = {}
}

watch([typeKey, typeVersion], async ([key, version], _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  versionDefinition.value = null
  loadingType.value = false
  if (!key || !version) return
  loadingType.value = true
  try {
    const type = types.value.find(item => item.type_key === key && item.version === version)
      ?? await getCredentialVersion(key, version)
    if (active) versionDefinition.value = type
  } catch (err) {
    if (active) error.value = credentialError(err, '无法加载类型版本')
  } finally {
    if (active) loadingType.value = false
  }
}, { immediate: true })

function suggestMappings() {
  const suggestions: Record<string, string> = {}
  for (const parameter of props.config.parameters) {
    const matching = fieldOptions(parameter.type).find(([name]) => name === parameter.name)
    if (matching) suggestions[parameter.name] = matching[0]
  }
  mappings.value = { ...suggestions, ...mappings.value }
}

async function perform(task: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  saved.value = ''
  try {
    await task()
  } catch (err) {
    error.value = credentialError(err, '操作失败，请刷新后重试')
  } finally {
    busy.value = false
  }
}

function save() {
  if (!canSave.value) return
  void perform(async () => {
    const resultFieldsWereSupported = Array.isArray(current.value?.model_result_fields)
    const response = await axios.put(credentialProfileUrl(props.toolId), {
      type_key: typeKey.value, type_version: typeVersion.value,
      selection_mode: selectionMode.value, mappings: Object.fromEntries(mapped.value),
      ...(selectionMode.value === 'fixed' ? { fixed_secret_id: fixedId.value || null } : {}),
      model_result_fields: splitMcpResultPaths(resultFieldText.value),
      expected_revision: current.value?.revision || 0, expected_profile_id: current.value?.profile_id,
      expected_result_target: props.config.model_result_policy?.target,
    })
    const profile = response.data.data as McpCredentialProfile
    current.value = profile
    const resultFieldsSupported = Array.isArray(profile.model_result_fields)
    const resultFieldsWarning = response.data.warnings?.find(
      (warning: { code: string }) => warning.code === 'MCP_RESULT_FIELDS_INVALID',
    )
    if (resultFieldsSupported && !resultFieldsWarning) resultFieldText.value = profile.model_result_fields.join('\n')
    emit('saved', profile)
    saved.value = profile.selection_mode === 'fixed' ? '凭据配置与个人固定绑定已保存' : '凭据配置已保存'
    if (resultFieldsWarning) {
      error.value = `结果字段配置有误，已设为模型不可见。${resultFieldsWarning.message}`
    } else if (!resultFieldsSupported && (resultFieldsWereSupported || resultFieldText.value.trim())) {
      error.value = '当前后端版本不支持保存结果字段，升级后请重新保存。'
    }
  })
}

onMounted(() => void perform(async () => {
  types.value = (await listCredentialTypes()).filter(item => isStructuredType(item.type_key))
  if (current.value && !types.value.some(item => item.type_key === current.value?.type_key)) {
    types.value.push(await getCredentialVersion(current.value.type_key, current.value.type_version))
  }
  if (!typeKey.value && types.value.length) {
    typeKey.value = types.value[0]!.type_key
    changeType()
  }
}))
</script>

<template>
  <div class="space-y-4 text-sm">
    <p v-if="config.stale" role="alert" class="rounded-lg bg-amber-50 px-3 py-2 text-amber-800">工具参数或目标已变化。重新保存配置后再试跑。</p>
    <p v-if="error" role="alert" class="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{{ error }}</p>
    <p v-if="saved" role="status" class="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800">{{ saved }}</p>
    <div class="grid gap-3 rounded-xl border bg-slate-50 p-3 sm:grid-cols-2">
      <label class="text-xs font-medium">凭据类型
        <select v-model="typeKey" :disabled="busy" class="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm" @change="changeType">
          <option value="" disabled>请选择</option>
          <option v-for="type in types" :key="type.type_key" :value="type.type_key">{{ type.name }}{{ type.enabled ? '' : ' · 已禁用' }}</option>
        </select>
      </label>
      <label class="text-xs font-medium">类型版本
        <select v-model.number="typeVersion" :disabled="busy" class="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm">
          <option v-for="version in versionOptions" :key="version" :value="version">v{{ version }}</option>
        </select>
      </label>
      <label class="text-xs font-medium">凭据选择方式
        <select v-model="selectionMode" :disabled="busy" class="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm">
          <option value="fixed">固定凭据</option>
          <option value="infer">由模型推导账号</option>
          <option value="ask">调用时授权</option>
        </select>
      </label>
      <p class="self-end pb-2 text-xs text-slate-600">
        <template v-if="selectionMode === 'infer'">模型结合对话上下文，在凭据中心按此类型选择账号；服务端校验类型和使用权限。</template>
        <template v-else-if="selectionMode === 'fixed'">在下方选择凭据，随设置一起保存；仅用于你自己的调用，模型无需选择账号。</template>
        <template v-else>每次调用由当前用户选择凭据并确认。</template>
      </p>
      <div v-if="selectionMode === 'fixed'" class="space-y-2 sm:col-span-2">
        <label class="block text-xs font-medium">我的固定凭据
          <select v-model="fixedId" aria-label="我的固定凭据"
            class="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm"
            :disabled="busy || loadingCandidates || !!candidateError" :aria-busy="loadingCandidates">
            <option value="">请选择凭据（留空则未绑定）</option>
            <option v-if="fixedUnavailable" :value="fixedId" disabled>已绑定的凭据当前不可用，请重新选择</option>
            <option v-for="item in candidates" :key="item.id" :value="item.id">{{ item.name }} · {{ item.account }}{{ item.owner_scope === 'system' ? ' · 系统共享' : '' }}</option>
          </select>
        </label>
        <p v-if="loadingCandidates" role="status" class="text-xs text-slate-600">正在加载可用凭据…</p>
        <div v-else-if="candidateError" role="alert" class="text-xs text-rose-700">
          {{ candidateError }}
          <button type="button" :disabled="busy" class="ml-2 rounded border px-2 py-1" @click="loadCandidates">重新加载</button>
        </div>
        <p v-else-if="fixedUnavailable" role="alert" class="text-xs text-amber-800">已绑定的凭据当前不可用，请重新选择；也可明确留空解除个人绑定。</p>
        <p v-else-if="!candidates.length" class="text-xs text-slate-600">暂无符合当前类型、版本和使用范围的可用凭据，请在凭据中心检查本人凭据或系统共享授权。</p>
        <p class="text-xs text-slate-600">只列出本人或已共享且可用于此工具的凭据。留空保存将解除你的个人固定绑定。</p>
      </div>
    </div>
    <div class="flex items-center justify-between">
      <h4 class="font-medium">字段映射</h4>
      <button type="button" :disabled="!versionDefinition || busy" class="rounded-lg border px-2 py-1 text-xs" @click="suggestMappings">按同名建议</button>
    </div>
    <div class="divide-y rounded-lg border">
      <div v-for="parameter in config.parameters" :key="parameter.name" class="grid grid-cols-2 items-center gap-2 p-2.5 text-xs">
        <div>
          <code>{{ parameter.name }}</code>
          <span class="ml-2 text-slate-500">{{ parameter.type || '未知类型' }}</span>
          <span v-if="parameter.group_binding_required && !mappings[parameter.name]" class="ml-2 text-rose-700">缺少必需映射</span>
          <span v-else-if="mappings[parameter.name]" class="ml-2 text-violet-700">服务端注入</span>
        </div>
        <select v-model="mappings[parameter.name]" :disabled="busy" :aria-label="parameter.name + ' 的凭据字段'"
          class="w-full rounded-lg border bg-white px-2 py-1.5">
          <option value="" :selected="!mappings[parameter.name]">模型填写</option>
          <option v-for="[name, field] in fieldOptions(parameter.type)" :key="name" :value="name">
            {{ name }}{{ field.writeOnly ? ' · 敏感字段' : '' }}
          </option>
        </select>
      </div>
    </div>
    <div class="space-y-2 rounded-lg border bg-slate-50 px-3 py-3">
      <label for="mcp-model-result-fields" class="block text-xs font-medium">模型可见的结果字段</label>
      <textarea id="mcp-model-result-fields" v-model="resultFieldText" :disabled="busy" rows="3"
        spellcheck="false" aria-describedby="mcp-model-result-help"
        placeholder="$.messages[*].subject"
        class="w-full rounded-lg border bg-white px-2.5 py-2 font-mono text-xs" />
      <div id="mcp-model-result-help" class="space-y-1 text-xs leading-relaxed text-slate-600">
        <p>每行填写一个 JSONPath（RFC 9535），最多 20 个。路径从解包后的业务 JSON 开始。</p>
        <ul class="list-disc space-y-0.5 pl-4">
          <li><code>$</code> 选择整份业务结果。</li>
          <li><code>$.messages[*]</code> 选择 <code>messages</code> 中的每封邮件或整行。</li>
          <li><code>$.messages[*][1]</code> 选择二维数组每行的第 2 列。</li>
          <li>业务结果有外层包装时，<code>$..messages[*]</code> 可在嵌套层级查找。</li>
        </ul>
        <p>留空、路径无效或无匹配时，只显示执行状态。按 JSONPath 选中的内容原样返回。</p>
        <p>所选内容可能出现在对话和执行追踪中。</p>
        <p v-if="config.stale" class="text-amber-800">工具参数或目标已变化，请按当前响应重新填写。</p>
      </div>
    </div>
    <details class="rounded-lg border bg-slate-50 px-3 py-2 text-xs">
      <summary class="cursor-pointer font-medium">模型参数预览</summary>
      <p class="mt-2">
        <code v-for="parameter in config.parameters.filter(item => !item.group_binding_required && !mappings[item.name])" :key="parameter.name"
          class="mr-2">{{ parameter.name }}</code>
        <code v-if="selectionMode === 'infer'" class="mr-2">credential_account（可选）</code>
      </p>
      <p v-if="selectionMode === 'infer' && typeKey && typeVersion" class="mt-2 text-slate-600">
        账号来自凭据中心的 {{ versionDefinition?.name || typeKey }}（{{ typeKey }}）v{{ typeVersion }}。
      </p>
      <p class="mt-2 text-slate-600">模型看不到凭据参数，只会收到执行状态及已配置的结果字段。</p>
    </details>
    <div class="flex justify-end">
      <button type="button" :disabled="!canSave" class="rounded-lg bg-blue-600 px-3 py-2 text-white disabled:opacity-50" @click="save">保存设置</button>
    </div>
    <p class="rounded-lg border bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
      可使用当前用户本人的凭据或已共享的系统凭据，仍须匹配工具要求的类型、版本、使用场景和目标域名。
      系统共享授权统一在凭据中心管理。
    </p>
  </div>
</template>
