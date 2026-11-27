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
import { computed, onMounted, ref } from 'vue'
import axios from '../../utils/axios'
import CredentialPicker from '../secrets/CredentialPicker.vue'
import SecretBindingLabel from '../secrets/SecretBindingLabel.vue'
import SecretFieldSelect from '../secrets/SecretFieldSelect.vue'
import SecretLocatorEditor, { type LocatorRule } from '../secrets/SecretLocatorEditor.vue'

/** 把绑定上的取用字段并回规则草稿：编辑已有规则时不能把它弄丢。 */
function withRulePath(rule: LocatorRule | null, valuePath?: string | null): LocatorRule | null {
  if (!rule) return null
  return { ...rule, value_path: valuePath || '' }
}

type Binding = {
  secret_id: string
  secret_key: string
  secret_name: string
  /** 绑定还在，但凭据可能已被停用/过期 */
  secret_status?: string
  /** 只取凭据的某个字段时给路径；空串 = 整段。结构化凭据必须给 */
  value_path?: string
  /** text | json —— 卡片上要标出「结构化 · 模板名」 */
  value_format?: string
  value_template?: string | null
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
  /** 规则命中的凭据要取哪个字段（空 = 整段） */
  value_path?: string | null
  bound_by_me: boolean
}

type McpTarget = {
  id: string
  server_name: string
  remark: string | null
  has_fixed_token: boolean
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
/** 绑定选中的字段路径（结构化凭据必填） */
const editValuePath = ref('')
/** 当前凭据是否「必须选一个字段」 */
const editNeedsField = ref(false)

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
    existingRule: withRulePath(target.rules?.[slot]?.rule ?? null, target.rules?.[slot]?.value_path),
  }
  locatorError.value = ''
}

/** 规则只写个人绑定：个人配的规则只影响自己，不会盖到别人 */
async function applyRule(rule: LocatorRule) {
  const ctx = locatorCtx.value
  // 先别关弹窗：原来在这里就置 null，保存失败（400/500）时弹窗直接消失、
  // locatorError 又没有人渲染，用户填的一整张规则表就这么没了，还不知道为什么。
  if (!ctx) return
  locatorBusy.value = true
  locatorError.value = ''
  const { value_path: rulePath = '', ...ruleBody } = rule
  try {
    await axios.post('/api/portal/secrets/bindings', {
      consumer_type: ctx.consumerType,
      consumer_id: ctx.consumerId,
      slot: ctx.slot,
      strategy: 'rule',
      // value_path 属于绑定记录，不属于规则 JSON——混进 rule 里后端会判未知字段
      rule: ruleBody,
      value_path: rulePath,
      // 不带 is_global：这就是「我自己的那条」
    })
    await load()
    // 存成功了才关
    locatorCtx.value = null
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
    // 删成功了才关规则窗（编辑器不再自己关）
    locatorCtx.value = null
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

/** 绑定弹窗打开时的已保存快照：用于判断当前选择是否已落库（check-binding 只校验已保存绑定） */
const savedSecretId = ref<string | null>(null)
const savedValuePath = ref('')

const canCheckSavedBinding = computed(() =>
  Boolean(
    editing.value
    && secretId.value
    && secretId.value === savedSecretId.value
    && (editValuePath.value || '') === (savedValuePath.value || ''),
  ),
)

/** 单槽位 MCP 服务的实际槽位（不再写死 header:Authorization，兼容单自定义头/单参数槽位） */
function singleSlotOf(target: McpTarget): string {
  return target.bindable_slots?.[0] || 'header:Authorization'
}

function singleBindingOf(target: McpTarget): Binding | null {
  const slot = singleSlotOf(target)
  return target.bindings?.[slot] || (slot === 'header:Authorization' ? target.binding : null) || null
}

/** 请求序号：追加分页与保存/解绑触发的刷新可能交错，后到的不许覆盖先到的。 */
let loadSeq = 0

async function load(append = false) {
  const seq = ++loadSeq
  if (append) {
    loadingMore.value = true
  } else {
    // 非追加刷新必须回到第 1 页，避免点击「加载更多」后保存/解绑把第 1 页列表盖掉
    page.value = 1
    loading.value = true
  }
  error.value = ''
  try {
    const res = await axios.get('/api/portal/secrets/platform-targets', {
      params: { page: page.value, size },
    })
    if (seq !== loadSeq) return
    const data = res.data?.data || {}
    const nextServers = data.mcp_servers || []
    const nextTools = data.api_tools || []
    servers.value = append ? [...servers.value, ...nextServers] : nextServers
    tools.value = append ? [...tools.value, ...nextTools] : nextTools
    const more = data.has_more || {}
    hasMore.value = Boolean(more.mcp_servers || more.api_tools)
  } catch (e: any) {
    if (seq !== loadSeq) return
    error.value = e?.response?.data?.detail || '加载失败'
    if (!append) {
      servers.value = []
      tools.value = []
      // 首屏失败必须把 hasMore 一起复位：否则列表已经空了，「加载更多」还在，
      // 点一下 page 直接跳到 2，第 1 页再也补不回来。
      hasMore.value = false
    }
  } finally {
    if (seq === loadSeq) {
      loading.value = false
      loadingMore.value = false
    }
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

function openBindMcp(target: McpTarget, slot?: string) {
  const resolvedSlot = slot || singleSlotOf(target)
  const existing = target.bindings?.[resolvedSlot] || (resolvedSlot === 'header:Authorization' ? target.binding : null) || null
  editing.value = { kind: 'mcp', target, slot: resolvedSlot }
  secretId.value = existing?.secret_id || null
  editValuePath.value = existing?.value_path || ''
  editNeedsField.value = false
  savedSecretId.value = existing?.secret_id || null
  savedValuePath.value = existing?.value_path || ''
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
  editValuePath.value = existing?.value_path || ''
  editNeedsField.value = false
  savedSecretId.value = existing?.secret_id || null
  savedValuePath.value = existing?.value_path || ''
  picked.value = existing
    ? { id: existing.secret_id, secret_key: existing.secret_key }
    : null
  bindError.value = ''
  checkResult.value = null
}

async function checkCurrent() {
  if (!editing.value || !secretId.value) return
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
  // 换凭据就清掉上一个凭据的字段路径：字段名在另一条凭据里通常不存在
  if (item?.id !== secretId.value) editValuePath.value = ''
}

/** 字段清单加载完：结构化且字段可读时必须选一个，否则注进去的是整段 JSON。 */
function onFieldsLoaded(info: { format: string; fields: { name: string }[] }) {
  editNeedsField.value = info.format === 'json' && info.fields.length > 0
}

async function submit() {
  if (!editing.value) return
  if (secretId.value && editNeedsField.value && !editValuePath.value) {
    bindError.value = '这条凭据是结构化的，请先选择要取用的字段'
    return
  }
  busy.value = true
  bindError.value = ''
  try {
    const target = editing.value
    const consumerType = target.kind === 'mcp' ? 'mcp_server' : 'api_tool'
    const consumerId = target.target.id
    const slot = editingSlot.value
    if (!secretId.value) {
      await axios.delete('/api/portal/secrets/bindings', {
        data: { consumer_type: consumerType, consumer_id: consumerId, slot },
      })
    } else {
      if (!picked.value) throw new Error('请选择凭据')
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: picked.value.id,
        consumer_type: consumerType,
        consumer_id: consumerId,
        slot,
        // 槽位可能是 header 也可能是指标参数；两种都靠它表达「取哪一部分」
        value_path: editValuePath.value,
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
              v-if="singleBindingOf(item)"
              class="flex shrink-0 flex-wrap items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-medium"
              :class="bindingIssue(singleBindingOf(item)) ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'"
            >我的凭据：
              <SecretBindingLabel
                :secret-key="singleBindingOf(item)!.secret_key"
                :format="singleBindingOf(item)!.value_format"
                :template="singleBindingOf(item)!.value_template"
                :value-path="singleBindingOf(item)!.value_path"
              />
              <template v-if="bindingIssue(singleBindingOf(item))">
                （{{ bindingIssue(singleBindingOf(item)) }}，调用会失败）
              </template>
            </span>
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
                >我的凭据：<SecretBindingLabel
                  :secret-key="item.bindings[slot]?.secret_key"
                  :format="item.bindings[slot]?.value_format"
                  :template="item.bindings[slot]?.value_template"
                  :value-path="item.bindings[slot]?.value_path"
                /><template v-if="bindingIssue(item.bindings[slot])">
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

            <!-- 单槽位：按实际唯一槽位操作，兼容非 Authorization 单自定义头或单参数槽位 -->
            <template v-else>
              <button
                type="button"
                class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50"
                @click="openBindMcp(item, singleSlotOf(item))"
              >{{ singleBindingOf(item) ? '更换凭据' : '绑定我的凭据' }}</button>
              <button
                type="button"
                class="shrink-0 rounded-lg border px-2.5 py-1 text-xs"
                :class="item.rules?.[singleSlotOf(item)]
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                  : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'"
                :title="item.rules?.[singleSlotOf(item)]?.rule_summary || '配一条规则，按你的身份自动选凭据'"
                @click="openLocatorFor('mcp', item, singleSlotOf(item))"
              >{{ item.rules?.[singleSlotOf(item)] ? '改规则' : '规则' }}</button>
            </template>
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
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="bindingIssue(tool.bindings[slot])
                    ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'"
                >我的凭据：<SecretBindingLabel
                  :secret-key="tool.bindings[slot]?.secret_key"
                  :format="tool.bindings[slot]?.value_format"
                  :template="tool.bindings[slot]?.value_template"
                  :value-path="tool.bindings[slot]?.value_path"
                /><template v-if="bindingIssue(tool.bindings[slot])">
                    （{{ bindingIssue(tool.bindings[slot]) }}，调用会失败）
                  </template></span>
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
            <CredentialPicker
              v-model="secretId"
              :scene="editing.kind === 'mcp' ? 'mcp' : 'api_tool'"
              @picked="onPicked"
            />
            <SecretFieldSelect
              v-if="secretId"
              v-model="editValuePath"
              :secret-id="secretId"
              @loaded="onFieldsLoaded"
            />
          </div>

          <div v-if="secretId" class="mt-2 space-y-1">
            <button
              type="button"
              :disabled="checking || !canCheckSavedBinding"
              class="rounded-md border border-gray-300 bg-white px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
              @click="checkCurrent"
            >{{ checking ? '检查中…' : '检查此刻能否取到' }}</button>
            <p v-if="!canCheckSavedBinding" class="text-[10px] leading-relaxed text-amber-600">
              当前选择尚未保存；请先点击「保存绑定」，保存后再检查此刻能否取到。
            </p>
            <p v-else-if="checkResult" :class="checkResult.ok ? 'text-[11px] text-emerald-700' : 'text-[11px] text-red-600'">
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
              {{ busy ? '保存中…' : secretId ? '保存绑定' : '解除绑定' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 规则编辑器：必须和「绑定凭据」弹窗**平级**，不能嵌进 v-if="editing" 里面。
         嵌进去的话，点「规则」只设 locatorCtx、不动 editing，整个子树不渲染，
         表现就是「点了没反应」。层级也要高于父弹窗（z-[100]/z-[110]）。 -->
    <SecretLocatorEditor
      :open="Boolean(locatorCtx)"
      :rule="locatorCtx ? locatorCtx.existingRule : null"
      :slot="locatorCtx ? locatorCtx.slot : ''"
      :consumer-type="locatorCtx ? locatorCtx.consumerType : ''"
      :arg-options="locatorCtx ? locatorCtx.argOptions : []"
      :allow-model-choice="locatorCtx ? !locatorCtx.slot.startsWith('header:') : true"
      :title="locatorCtx ? `凭据匹配规则 · ${locatorCtx.title}` : '凭据匹配规则'"
      @update:open="(value: boolean) => { if (!value) locatorCtx = null }"
      removable
      @save="applyRule"
      @remove="onRemoveRule"
    />

    <!-- 规则保存/删除失败必须看得见。弹窗关掉之后如果没有任何提示，用户只会以为
         自己没点上，然后重填一遍同样的规则。 -->
    <div
      v-if="locatorError"
      class="fixed inset-x-0 top-4 z-[130] mx-auto w-fit max-w-2xl rounded-md border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-700 shadow-lg"
      role="alert"
    >
      {{ locatorError }}
      <button class="ml-3 font-semibold text-rose-500 hover:text-rose-700" @click="locatorError = ''">
        知道了
      </button>
    </div>
  </div>
</template>
