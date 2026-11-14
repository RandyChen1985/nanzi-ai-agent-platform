<script setup lang="ts">
/**
 * 「我的 MCP → 平台服务」：给平台级的 MCP 服务绑自己的凭据。
 *
 * 为什么单独做一块：平台 MCP 的列表接口只给管理员（普通用户请求会 403），
 * 所以用户在自己的「我的 MCP」里根本看不到这些服务，也就没有地方绑凭据。
 * 管理员配置的固定 Token 是全平台共用的——一旦有人需要用自己的账号调用，
 * 就必须有一个入口，否则「每个人用自己的 Token」这个能力只有管理员用得上。
 *
 * 这里**只做绑定这一件事**：不显示地址、不能改服务、不能看工具。用户的权限
 * 应当刚好够用，而不是把整个管理面板借给他。
 */
import { computed, onMounted, ref, watch } from 'vue'
import FixedMcpCredentialDialog from './FixedMcpCredentialDialog.vue'
import { credentialError, getCredentialVersion, type CredentialMeta, type CredentialField } from '@/composables/useCredentialFields'
import axios from '../../utils/axios'
import CredentialPicker from '../secrets/CredentialPicker.vue'
import SecretLocatorEditor, { type LocatorRule } from '../secrets/SecretLocatorEditor.vue'

type Binding = {
  secret_id: string
  secret_key: string
  secret_name: string
  /** 绑定还在，但凭据可能已被停用/过期 */
  secret_status?: string
  value_field?: string | null
}

/** 绑了却用不了，比没绑更该提示——否则用户只会看到调用失败。 */
const bindingIssue = (binding?: Binding | null): string => {
  if (!binding || !binding.secret_status || binding.secret_status === 'active') return ''
  if (binding.secret_status === 'disabled') return '已停用'
  if (binding.secret_status === 'expired') return '已过期'
  return '不可用'
}

/** 一条定位规则（不属于「绑定到某条凭据」，所以单独一个类型） */
type BoundRule = {
  rule: LocatorRule | null
  rule_summary: string | null
  bound_by_me: boolean
}

type McpTarget = {
  id: string
  server_name: string
  remark: string | null
  has_fixed_token: boolean
  group_tools?: Array<{ id: string; name: string }>
  bindable_slots: string[]
  bindings: Record<string, Binding | null>
  /** 槽位 → 我配的规则（与固定绑定并存） */
  rules?: Record<string, BoundRule | null>
  /** 槽位 → 可当规则键的入参名 */
  arg_options?: Record<string, string[]>
  /** 旧字段：只有 Authorization 的那一个绑定 */
  binding: Binding | null
}

type ToolTarget = {
  id: string
  name: string
  description: string | null
  bindable_slots: string[]
  bindings: Record<string, Binding | null>
  rules?: Record<string, BoundRule | null>
  arg_options?: Record<string, string[]>
}

const servers = ref<McpTarget[]>([])
const tools = ref<ToolTarget[]>([])
const loading = ref(false)
const error = ref('')

/** 正在绑定谁：MCP 服务固定一个槽位，API 工具按 header 名逐槽位绑。 */
const editing = ref<
  | { kind: 'mcp'; target: McpTarget; slot: string }
  | { kind: 'tool'; target: ToolTarget; slot: string }
  | null
>(null)
const picked = ref<{ id: string; secret_key: string } | null>(null)
const secretId = ref<string | null>(null)
const busy = ref(false)
const bindError = ref('')
const groupTarget = ref<{ id: string; name: string } | null>(null)
const typedMode = ref(false)
const typedId = ref('')
const valueField = ref('')
const fields = ref<Record<string, CredentialField>>({})
const structured = ref<Array<CredentialMeta & { id: string; name: string; allowed_scenarios: string[]; status: string }>>([])
const typedChoices = computed(() => {
  const target = editing.value
  if (!target) return []
  return structured.value.filter(item => item.credential_format === 'structured_v1' && item.status === 'active'
    && (target.kind === 'tool' ? /^(header|body):/.test(target.slot) && item.allowed_scenarios.includes('api_tool')
      : target.slot.startsWith('header:') && item.type_key === 'mcp_token' && item.allowed_scenarios.includes('mcp')))
})
const typedFields = computed(() => Object.entries(fields.value).filter(([name, field]) =>
  editing.value?.kind === 'mcp' ? name === 'token' : !editing.value?.slot.startsWith('header:') || field.type === 'string',
))

watch(editing, async (target, _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  structured.value = []
  fields.value = {}
  const binding = target?.target.bindings?.[target.slot]
  typedMode.value = Boolean(binding?.value_field)
  typedId.value = binding?.value_field ? binding.secret_id : ''
  valueField.value = binding?.value_field || ''
  if (!target || (target.kind === 'mcp' && !target.slot.startsWith('header:'))) return
  try {
    const response = await axios.get('/api/portal/secrets', { params: { scope: 'mine' } })
    if (active) structured.value = response.data?.data?.items || []
  } catch (err) {
    if (active && typedMode.value) bindError.value = credentialError(err, '无法加载结构化凭据')
  }
})

watch([typedId, structured], async ([id], _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  fields.value = {}
  const item = structured.value.find(row => row.id === id)
  if (!item?.type_key || !item.type_version) return
  try {
    const type = await getCredentialVersion(item.type_key, item.type_version)
    if (!active) return
    fields.value = type.schema.properties
    if (editing.value?.kind === 'mcp') valueField.value = 'token'
    else if (valueField.value && !fields.value[valueField.value]) valueField.value = ''
  } catch (err) {
    if (active) bindError.value = credentialError(err, '无法加载凭据字段')
  }
})

/** 正在给哪个槽位配规则 */
const locatorCtx = ref<{
  kind: 'mcp' | 'tool'
  consumerType: 'mcp_server' | 'api_tool'
  consumerId: string
  title: string
  slot: string
  argOptions: string[]
  /** 已有规则，打开时回填 */
  existingRule: LocatorRule | null
} | null>(null)
const locatorBusy = ref(false)
const locatorError = ref('')

function openLocatorFor(
  kind: 'mcp' | 'tool',
  target: McpTarget | ToolTarget,
  slot: string,
) {
  locatorCtx.value = {
    kind,
    consumerType: kind === 'mcp' ? 'mcp_server' : 'api_tool',
    consumerId: String(target.id),
    // 两种目标的名称字段不同，这里的 kind 已经决定了实际类型
    title: kind === 'mcp' ? (target as McpTarget).server_name : (target as ToolTarget).name,
    slot,
    argOptions: target.arg_options?.[slot] || [],
    existingRule: target.rules?.[slot]?.rule || null,
  }
  locatorError.value = ''
}

/** 规则只写个人绑定：个人配的规则只影响自己，不会盖到别人 */
async function applyRule(rule: LocatorRule) {
  const ctx = locatorCtx.value
  locatorCtx.value = null
  if (!ctx) return
  locatorBusy.value = true
  locatorError.value = ''
  try {
    await axios.post('/api/portal/secrets/bindings', {
      consumer_type: ctx.consumerType,
      consumer_id: ctx.consumerId,
      slot: ctx.slot,
      strategy: 'rule',
      rule,
      // 不带 is_global：这就是「我自己的那条」
    })
    await load()
  } catch (e: any) {
    locatorError.value = e?.response?.data?.detail || '保存规则失败'
  } finally {
    locatorBusy.value = false
  }
}

/** 删除规则：槽位与消费者从当前上下文取，避免模板里再传一遍 */
async function onRemoveRule() {
  const ctx = locatorCtx.value
  if (!ctx) return
  await clearRuleById(ctx.consumerType, ctx.consumerId, ctx.slot)
}

async function clearRuleById(consumerType: string, consumerId: string, slot: string) {
  locatorBusy.value = true
  locatorError.value = ''
  try {
    await axios.delete('/api/portal/secrets/bindings', {
      data: { consumer_type: consumerType, consumer_id: consumerId, slot, strategy: 'rule' },
    })
    await load()
  } catch (e: any) {
    locatorError.value = e?.response?.data?.detail || '解除规则失败'
  } finally {
    locatorBusy.value = false
  }
}

const page = ref(1)
const size = 50
const hasMore = ref(false)
const loadingMore = ref(false)

async function load(append = false) {
  if (append) loadingMore.value = true
  else loading.value = true
  error.value = ''
  try {
    const res = await axios.get('/api/portal/secrets/platform-targets', {
      params: { page: page.value, size },
    })
    const data = res.data?.data || {}
    const nextServers = data.mcp_servers || []
    const nextTools = data.api_tools || []
    servers.value = append ? [...servers.value, ...nextServers] : nextServers
    tools.value = append ? [...tools.value, ...nextTools] : nextTools
    const more = data.has_more || {}
    hasMore.value = Boolean(more.mcp_servers || more.api_tools)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载失败'
    if (!append) {
      servers.value = []
      tools.value = []
    }
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

function loadMore() {
  page.value += 1
  void load(true)
}

/** 「试一次」的结果：只回答能不能取到，不发任何出站请求。 */
const checking = ref(false)
const checkResult = ref<{ ok: boolean; reason: string | null } | null>(null)

/** 槽位给人看的名字。`arg:工具:参数` 直接摊给用户只会让人猜。 */
function slotLabel(slot: string): string {
  const [prefix, ...rest] = slot.split(':')
  const tail = rest.join(':')
  if (prefix === 'header') return `请求头 · ${tail}`
  if (prefix === 'arg') return `参数 · ${tail}`
  if (prefix === 'query') return `查询参数 · ${tail}`
  if (prefix === 'body') return `请求体 · ${tail}`
  if (prefix === 'env') return `环境变量 · ${tail}`
  return slot
}

function openBindMcp(target: McpTarget, slot = 'header:Authorization') {
  const existing = target.bindings?.[slot] || target.binding || null
  editing.value = { kind: 'mcp', target, slot }
  secretId.value = existing?.secret_id || null
  picked.value = existing
    ? { id: existing.secret_id, secret_key: existing.secret_key }
    : null
  bindError.value = ''
  checkResult.value = null
}

function openBindTool(target: ToolTarget, slot: string) {
  const existing = target.bindings?.[slot] || null
  editing.value = { kind: 'tool', target, slot }
  secretId.value = existing?.secret_id || null
  picked.value = existing
    ? { id: existing.secret_id, secret_key: existing.secret_key }
    : null
  bindError.value = ''
  checkResult.value = null
}

async function checkCurrent() {
  if (!editing.value || !(typedMode.value ? typedId.value : secretId.value)) return
  checking.value = true
  checkResult.value = null
  try {
    const target = editing.value
    const res = await axios.post('/api/portal/secrets/check-binding', {
      consumer_type: target.kind === 'mcp' ? 'mcp_server' : 'api_tool',
      consumer_id: target.target.id,
      slot: editingSlot.value,
      scene: target.kind === 'mcp' ? 'mcp' : 'api_tool',
    })
    const data = res.data?.data || {}
    checkResult.value = { ok: Boolean(data.ok), reason: data.reason || null }
  } catch (e: any) {
    checkResult.value = { ok: false, reason: e?.response?.data?.detail || '校验失败' }
  } finally {
    checking.value = false
  }
}

const editingSlot = computed(() => {
  if (!editing.value) return ''
  // API 工具的槽位是后端算好的完整形态（header:/query:/body:）；
  // MCP 的也是完整形态。两边都不再拼前缀。
  return editing.value.slot
})

const editingTitle = computed(() => {
  if (!editing.value) return ''
  const name = editing.value.kind === 'mcp'
    ? editing.value.target.server_name
    : editing.value.target.name
  // 走 slotLabel：`arg:query:api_key` 这种底层槽位直抛给用户只会让人猜
  return `${name} · ${slotLabel(editing.value.slot)}`
})

function onPicked(item: { id: string; secret_key: string } | null) {
  picked.value = item
}

async function submit() {
  if (!editing.value) return
  busy.value = true
  bindError.value = ''
  try {
    const target = editing.value
    const consumerType = target.kind === 'mcp' ? 'mcp_server' : 'api_tool'
    const consumerId = target.target.id
    const slot = editingSlot.value
    const chosenId = typedMode.value ? typedId.value : secretId.value
    if (!chosenId) {
      await axios.delete('/api/portal/secrets/bindings', {
        data: { consumer_type: consumerType, consumer_id: consumerId, slot },
      })
    } else {
      if (typedMode.value && !valueField.value) {
        bindError.value = '请选择要注入的字段'
        return
      }
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: chosenId,
        ...(typedMode.value ? { value_field: valueField.value } : {}),
        consumer_type: consumerType,
        consumer_id: consumerId,
        slot,
      })
    }
    editing.value = null
    await load()
  } catch (e: any) {
    bindError.value = e?.response?.data?.detail || e?.message || '保存失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="min-h-0 flex-1 overflow-y-auto">
    <div class="mb-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-700">
      <b>平台服务由管理员统一配置。</b>这里绑的凭据只对<strong>你自己</strong>的调用生效，
      会盖过管理员配的固定 Token，其他人不受影响。想让所有人共用一个值，
      可以请管理员绑一条<strong>系统级凭据</strong>到同一个位置——那样能轮换，
      比在服务上写死 Token 更好维护。
    </div>

    <p v-if="loading" class="py-10 text-center text-sm text-gray-400">加载中…</p>
    <p v-else-if="error" class="py-10 text-center text-sm text-red-500">{{ error }}</p>
    <p
      v-else-if="!servers.length && !tools.length"
      class="rounded-xl border border-dashed border-gray-300 py-14 text-center text-sm text-gray-500"
    >
      平台还没有配置可供个人绑定的 MCP 服务或 API 工具
    </p>

    <template v-else>
      <section v-if="servers.length" class="mb-4">
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">MCP 服务</h4>
        <ul class="space-y-2">
          <li
            v-for="item in servers"
            :key="`mcp-${item.id}`"
            class="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white px-3 py-2.5"
          >
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium text-gray-900">{{ item.server_name }}</p>
              <p v-if="item.remark" class="mt-0.5 truncate text-[11px] text-gray-400">{{ item.remark }}</p>
            </div>

            <span
              v-if="item.binding"
              class="shrink-0 rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700"
            >我的凭据：{{ item.binding.secret_key }}</span>
            <span
              v-else-if="item.has_fixed_token"
              class="shrink-0 rounded-md bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600"
            >使用管理员配的 Token</span>
            <span v-else class="shrink-0 rounded-md bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">无鉴权</span>
            <!-- 槽位：通常只有 Authorization，但服务配了别的认证头时也要能各自绑 -->
            <div v-if="item.bindable_slots.length > 1" class="mt-2 w-full space-y-1.5">
              <div
                v-for="slot in item.bindable_slots"
                :key="slot"
                class="flex flex-wrap items-center gap-2 rounded-lg bg-gray-50 px-2 py-1.5"
              >
                <code class="text-[11px] text-gray-700" :title="slot">{{ slotLabel(slot) }}</code>
                <span
                  v-if="item.bindings?.[slot]"
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="bindingIssue(item.bindings[slot])
                    ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'"
                >我的凭据：{{ item.bindings[slot]?.secret_key
                  }}<template v-if="bindingIssue(item.bindings[slot])">
                    （{{ bindingIssue(item.bindings[slot]) }}，调用会失败）
                  </template></span>
                <span v-else class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">未绑</span>
                <button
                  type="button"
                  class="ml-auto shrink-0 rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-50"
                  @click="openBindMcp(item, slot)"
                >{{ item.bindings?.[slot] ? '更换' : '绑定' }}</button>
                <button
                  type="button"
                  class="shrink-0 rounded-md border px-2 py-0.5 text-[11px]"
                  :class="item.rules?.[slot]
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                    : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'"
                  :title="item.rules?.[slot]?.rule_summary || '配一条规则，按这次调用的入参自动选凭据'"
                  @click="openLocatorFor('mcp', item, slot)"
                >{{ item.rules?.[slot] ? '改规则' : '规则' }}</button>
              </div>
            </div>

            <!-- 单槽位（大多数服务只有 Authorization）：两个按钮共用一个 v-else 分支，
                 给第二个按钮再写一个 v-else 会编译失败（v-else 必须紧邻 v-if/v-else-if） -->
            <template v-else>
              <button
                type="button"
                class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50"
                @click="openBindMcp(item)"
              >{{ item.binding ? '更换凭据' : '绑定我的凭据' }}</button>
              <button
                type="button"
                class="shrink-0 rounded-lg border px-2.5 py-1 text-xs"
                :class="item.rules?.['header:Authorization']
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                  : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'"
                :title="item.rules?.['header:Authorization']?.rule_summary || '配一条规则，按你的身份自动选凭据'"
                @click="openLocatorFor('mcp', item, 'header:Authorization')"
              >{{ item.rules?.['header:Authorization'] ? '改规则' : '规则' }}</button>
            </template>
            <div v-if="item.group_tools?.length" class="mt-2 flex w-full flex-wrap gap-2 border-t pt-2">
              <button v-for="tool in item.group_tools" :key="tool.id" type="button"
                class="rounded-lg border px-2 py-1 text-xs text-blue-700 hover:bg-blue-50" @click="groupTarget = tool">
                {{ tool.name }} · 固定凭据
              </button>
            </div>
          </li>
        </ul>
      </section>

      <section v-if="tools.length">
        <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">API 工具</h4>
        <ul class="space-y-2">
          <li
            v-for="tool in tools"
            :key="`tool-${tool.id}`"
            class="rounded-xl border border-gray-200 bg-white px-3 py-2.5"
          >
            <div class="flex flex-wrap items-center gap-2">
              <p class="min-w-0 flex-1 truncate text-sm font-medium text-gray-900">{{ tool.name }}</p>
              <span
                v-if="!tool.bindable_slots.length"
                class="shrink-0 rounded-md bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500"
              >没有可绑的位置</span>
            </div>
            <p v-if="tool.description" class="mt-0.5 truncate text-[11px] text-gray-400">{{ tool.description }}</p>

            <div v-if="tool.bindable_slots.length" class="mt-2 space-y-1.5">
              <div
                v-for="slot in tool.bindable_slots"
                :key="slot"
                class="flex flex-wrap items-center gap-2 rounded-lg bg-gray-50 px-2 py-1.5"
              >
                <code class="text-[11px] text-gray-700" :title="slot">{{ slotLabel(slot) }}</code>
                <span
                  v-if="tool.bindings?.[slot]"
                  class="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700"
                >我的凭据：{{ tool.bindings[slot]?.secret_key }}</span>
                <span v-else class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">未绑</span>
                <button
                  type="button"
                  class="ml-auto shrink-0 rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-50"
                  @click="openBindTool(tool, slot)"
                >{{ tool.bindings?.[slot] ? '更换' : '绑定' }}</button>
                <button
                  type="button"
                  class="shrink-0 rounded-md border px-2 py-0.5 text-[11px]"
                  :class="tool.rules?.[slot]
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                    : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'"
                  :title="tool.rules?.[slot]?.rule_summary || '配一条规则，按这次调用的入参自动选凭据'"
                  @click="openLocatorFor('tool', tool, slot)"
                >{{ tool.rules?.[slot] ? '改规则' : '规则' }}</button>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <div v-if="hasMore" class="mt-3 text-center">
        <button
          type="button"
          :disabled="loadingMore"
          class="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-60"
          @click="loadMore"
        >{{ loadingMore ? '加载中…' : '加载更多' }}</button>
      </div>
    </template>

    <Teleport to="body">
      <div
        v-if="editing"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="editing = null"
      >
        <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
          <h3 class="text-base font-semibold text-gray-900">
            为 <code class="rounded bg-gray-100 px-1.5 py-0.5 text-sm">{{ editingTitle }}</code> 绑定凭据
          </h3>
          <p class="mt-1 text-xs leading-relaxed text-gray-500">
            只影响你自己的调用。真实值不写进服务配置，调用时按当前用户现取。
          </p>

          <div class="mt-3 space-y-2">
            <div v-if="typedChoices.length || typedMode" class="flex gap-2 text-xs">
              <button type="button" class="rounded-lg border px-2 py-1" :class="!typedMode ? 'bg-blue-50' : ''" @click="typedMode = false">单值凭据</button>
              <button type="button" class="rounded-lg border px-2 py-1" :class="typedMode ? 'bg-blue-50' : ''" @click="typedMode = true">结构化字段</button>
            </div>
            <CredentialPicker v-if="!typedMode" v-model="secretId" :scene="editing.kind === 'mcp' ? 'mcp' : 'api_tool'" @picked="onPicked" />
            <template v-else>
              <select v-model="typedId" aria-label="选择结构化凭据" class="w-full rounded-lg border p-2 text-sm" @change="valueField = ''">
                <option value="">不绑定</option>
                <option v-for="item in typedChoices" :key="item.id" :value="item.id">{{ item.name }} · {{ item.account }}</option>
              </select>
              <select v-if="typedId" v-model="valueField" aria-label="选择字段" class="w-full rounded-lg border p-2 text-sm">
                <option value="">请选择字段</option>
                <option v-for="[name, field] in typedFields" :key="name" :value="name">{{ field.title || name }}{{ field.writeOnly ? ' · 敏感字段' : '' }}</option>
              </select>
            </template>
          </div>

          <div v-if="typedMode ? typedId : secretId" class="mt-2 space-y-1">
            <button
              type="button"
              :disabled="checking"
              class="rounded-md border border-gray-300 bg-white px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50 disabled:opacity-60"
              @click="checkCurrent"
            >{{ checking ? '检查中…' : '检查此刻能否取到' }}</button>
            <p v-if="checkResult" :class="checkResult.ok ? 'text-[11px] text-emerald-700' : 'text-[11px] text-red-600'">
              {{ checkResult.ok ? '✓ 现在能取到，调用时会注入' : `✗ 取不到：${checkResult.reason}` }}
            </p>
            <p class="text-[10px] leading-relaxed text-gray-400">
              只走解析链路，不会真的发起请求，也不会显示凭据内容。
            </p>
          </div>

          <p v-if="bindError" class="mt-2 text-[11px] text-red-600">{{ bindError }}</p>

          <div class="mt-4 flex justify-end gap-2">
            <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="editing = null">
              取消
            </button>
            <button
              type="button"
              :disabled="busy"
              class="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
              @click="submit"
            >
              {{ busy ? '保存中…' : (typedMode ? typedId : secretId) ? '保存绑定' : '解除绑定' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <FixedMcpCredentialDialog v-if="groupTarget" :tool-id="groupTarget.id" :tool-name="groupTarget.name" @close="groupTarget = null" />

    <!-- 规则编辑器：必须和「绑定凭据」弹窗**平级**，不能嵌进 v-if="editing" 里面。
         嵌进去的话，点「规则」只设 locatorCtx、不动 editing，整个子树不渲染，
         表现就是「点了没反应」。层级也要高于父弹窗（z-[100]/z-[110]）。 -->
    <SecretLocatorEditor
      :open="Boolean(locatorCtx)"
      :rule="locatorCtx ? locatorCtx.existingRule : null"
      :slot="locatorCtx ? locatorCtx.slot : ''"
      :arg-options="locatorCtx ? locatorCtx.argOptions : []"
      :title="locatorCtx ? `凭据匹配规则 · ${locatorCtx.title}` : '凭据匹配规则'"
      @update:open="(value: boolean) => { if (!value) locatorCtx = null }"
      removable
      @save="applyRule"
      @remove="onRemoveRule"
    />
  </div>
</template>
