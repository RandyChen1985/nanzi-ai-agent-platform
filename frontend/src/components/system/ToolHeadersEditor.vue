<script setup lang="ts">
/**
 * 请求 Headers 编辑器：默认键值对，复杂场景保留原始 JSON。
 *
 * 只接受 JSON 对象，不猜数组/字符串等形状；值统一按字符串保存。键的重复由
 * 浏览器对象语义自然收敛，界面在保存前会提示空键与重复键。
 *
 * 凭据中心的接入点在每一行的「凭据」按钮：选中一条凭据后，这一行的值会被换成
 * ``${CREDENTIAL:<标识>}`` 占位符，同时写一条 ``sys_secret_bindings`` 记录
 * （槽位 ``header:<名称>``）。占位符是给人看的标记，运行时的真实值由注入层补。
 *
 * **新建工具时也能先选**：绑定的消费者是工具 id，工具还没入库时先记在
 * ``pendingBindings`` 里，保存拿到 id 后由父组件调用 ``flushPending()`` 补写。
 * 否则用户得先存一遍空配置、再回来改——和之前 MCP 那边一样的坑。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { KeyIcon, TrashIcon } from '@heroicons/vue/24/outline'
import axios from '../../utils/axios'
import type { CredentialPickerItem } from '../../utils/credentialPicker'
import CredentialPicker from '../secrets/CredentialPicker.vue'
import SecretFieldSelect from '../secrets/SecretFieldSelect.vue'
import SecretBindingLabel from '../secrets/SecretBindingLabel.vue'

const props = withDefaults(defineProps<{
    /**
     * 这个消费者是不是平台级（配一次给所有人用）。
     *
     * 由消费端声明，组件不猜——写死 `true` 在「个人级 API 工具」出现那天
     * 会把个人工具上的绑定误设成全局。
     */
    isGlobal?: boolean
  modelValue: string
  error?: string
  /** 绑定的消费者类型；默认 API 工具 */
  consumerType?: string
  /** 工具 id；为空表示还没保存，凭据绑定不可用 */
  consumerId?: string

}>(), {
  isGlobal: true,
  error: '',
  consumerType: 'api_tool',
  consumerId: '',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

type HeaderRow = { id: number; key: string; value: string }

let rowSeed = 0
const rows = ref<HeaderRow[]>([])
const mode = ref<'rows' | 'json'>('rows')
const parseError = ref('')
const inputRefs = new Map<number, HTMLInputElement>()
let syncing = false

const displayError = computed(() => props.error || parseError.value)

const parseHeaders = (text: string): Record<string, string> | null => {
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      parseError.value = 'Headers 必须是 JSON 对象，例如 {"Authorization": "Bearer ..."}'
      return null
    }
    const headers: Record<string, string> = {}
    for (const [rawKey, rawValue] of Object.entries(parsed)) {
      const key = String(rawKey).trim()
      if (!key) continue
      headers[key] = typeof rawValue === 'string' ? rawValue : String(rawValue)
    }
    parseError.value = ''
    return headers
  } catch {
    parseError.value = 'Headers 不是合法 JSON，请先修正'
    return null
  }
}

const rowsToJson = (): string => {
  const headers: Record<string, string> = {}
  for (const row of rows.value) {
    const key = row.key.trim()
    if (!key) continue
    // 同名 Header 以最后一行为准，序列化后是唯一对象键。
    headers[key] = row.value
  }
  return JSON.stringify(headers, null, 2)
}

const syncRows = (text: string) => {
  const parsed = parseHeaders(text)
  if (!parsed) {
    return
  }
  rows.value = Object.entries(parsed).map(([key, value]) => ({
    id: ++rowSeed,
    key,
    value,
  }))
}

const emitFromRows = () => {
  const json = rowsToJson()
  syncing = true
  emit('update:modelValue', json)
  // 让父组件回写的 props 再触发一次 watch 时不会把正在编辑的行重建掉。
  void nextTick(() => { syncing = false })
}

const onKeyInput = (row: HeaderRow, event: Event) => {
  row.key = (event.target as HTMLInputElement).value
  emitFromRows()
}

const onValueInput = (row: HeaderRow, event: Event) => {
  row.value = (event.target as HTMLInputElement).value
  emitFromRows()
}

const addRow = () => {
  const row = { id: ++rowSeed, key: '', value: '' }
  rows.value.push(row)
  void nextTick(() => inputRefs.get(row.id)?.focus())
}

const removeRow = (id: number) => {
  rows.value = rows.value.filter((row) => row.id !== id)
  emitFromRows()
}

const switchToRows = () => {
  const parsed = parseHeaders(props.modelValue)
  if (!parsed) return
  syncRows(props.modelValue)
  mode.value = 'rows'
}

const switchToJson = () => {
  mode.value = 'json'
}

// ── 凭据绑定 ──────────────────────────────────────────────────────────
type BindingInfo = {
  secret_id: string
  secret_key: string
  secret_name: string
  /** 后端返回的凭据状态；绑定还在，但凭据可能已被停用/过期 */
  secret_status?: string
  /** 只取凭据的某个字段时给路径；空串 = 整段。结构化凭据必须给 */
  value_path?: string
  /** text | json —— 弹窗里也要标出「结构化 · 模板名」 */
  value_format?: string
  value_template?: string | null
  /** user | system：重开弹窗时判断「是不是只对我生效」 */
  owner_scope?: string
}

/** 凭据失效的提示文案：绑了却用不了，比没绑更让人困惑。 */
function bindingIssue(binding: BindingInfo | null): string {
  if (!binding || !binding.secret_status || binding.secret_status === 'active') return ''
  if (binding.secret_status === 'disabled') return '凭据已停用'
  if (binding.secret_status === 'expired') return '凭据已过期'
  return '凭据不可用'
}

const bindings = ref<Record<string, BindingInfo>>({})
const bindingSlot = ref<string | null>(null)
const bindingSecretId = ref<string | null>(null)
/** 选中的凭据：要带上形态信息，新建态记账才不会丢掉「结构化 / 模板」 */
type PickedSecret = {
  id: string
  secret_key: string
  /** user | system：平台级工具上选个人凭据只有本人能用 */
  owner_scope?: string
  value_format?: string
  value_template?: string | null
}
const pickedItem = ref<PickedSecret | null>(null)
const bindingBusy = ref(false)
const bindingError = ref('')
/** 绑定时选中的字段路径（结构化凭据必填） */
const bindingValuePath = ref('')
/** 当前凭据的字段清单是否「必须选一个」 */
const bindingNeedsField = ref(false)

/**
 * 新建态选的绑定。键是完整槽位（``header:X-Tenant`` / ``query:api_key``），
 * 值和落库后的形状一致，补写时直接用。
 */
const pendingBindings = ref<Record<string, BindingInfo>>({})

/** 取某个 header 的绑定；模板里不能靠三元表达式跨分支收窄类型，所以走函数。 */
const bindingOf = (key: string): BindingInfo | null => {
  const slot = `header:${key.trim()}`
  return bindings.value[slot] || pendingBindings.value[slot] || null
}

const bindingSlotName = computed(() => (bindingSlot.value || '').replace(/^header:/, ''))
/**
 * 「平台的工具 + 你自己的凭据 = 只有你能用」。
 *
 * API 工具是平台级消费者，header 绑定提交时带 `is_global`；选中个人凭据
 * 后端会降级成本人绑定，别人调用时这个 header 拿不到值。必须在选中那一刻讲清楚。
 */
const personalScopeNotice = computed(() => {
  // 个人级工具上绑自己的凭据是正常的，不该跳这个提示
  if (!props.isGlobal) return ''
  const item = bindingSecretId.value ? pickedItem.value : null
  if (!item || item.owner_scope !== 'user') return ''
  return (
    '这是你自己的凭据，绑定只对你生效：别人调用这个工具时这个 Header 拿不到值。' +
    '要让每人用自己的账号，请改用「按规则匹配」。'
  )
})

const currentBinding = computed(() => {
  if (!bindingSlot.value) return null
  // 也要看 pending：新建态（工具还没有 id）绑完不会写 bindings，
  // 重开弹窗时底部就少一行「当前已绑定 …」，看起来像是没绑上。
  return (
    bindings.value[bindingSlot.value] || pendingBindings.value[bindingSlot.value] || null
  )
})

async function loadBindings() {
  if (!props.consumerId) {
    bindings.value = {}
    return
  }
  try {
    const res = await axios.get('/api/portal/secrets/bindings', {
      params: { consumer_type: props.consumerType, consumer_id: props.consumerId },
    })
    const next: Record<string, BindingInfo> = {}
    for (const row of res.data?.data || []) {
      // 全局绑定用 is_global 判，不用 owner_scope——后者对没有 secret_id 的行
      // （规则绑定）是 null，会把这些行静默丢掉。这里现在只有固定绑定，
      // 但口径要和 ToolSystemParamEditor 一致，免得加规则入口时再踩一次。
      if (!row.bound_by_me && !row.is_global) continue
      next[row.slot] = {
        secret_id: row.secret_id,
        secret_key: row.secret_key,
        // 少了这个，重开弹窗时「只对你生效」判不出来（personalScopeNotice 靠它）
        owner_scope: row.owner_scope,
        value_format: row.value_format || 'text',
        value_template: row.value_template || null,
        secret_name: row.secret_name,
        secret_status: row.secret_status,
        value_path: row.value_path || '',
      }
    }
    bindings.value = next
  } catch {
    // 绑定信息拿不到不该挡住编辑 Headers，按「没绑」显示即可
    bindings.value = {}
  }
}

function openBinding(row: HeaderRow) {
  const key = row.key.trim()
  if (!key) return
  bindingSlot.value = `header:${key}`
  const existing = bindings.value[bindingSlot.value] || pendingBindings.value[bindingSlot.value]
  bindingSecretId.value = existing?.secret_id || null
  bindingValuePath.value = existing?.value_path || ''
  // owner_scope 也要带上：不带的话重开弹窗时 personalScopeNotice 判不出来，
  // 用户会以为这条绑定对所有人生效。
  pickedItem.value = existing
    ? {
        id: existing.secret_id,
        secret_key: existing.secret_key,
        owner_scope: existing.owner_scope,
        value_format: existing.value_format,
        value_template: existing.value_template,
      }
    : null
  bindingError.value = ''
}

function onPicked(item: CredentialPickerItem | null) {
  pickedItem.value = item
  // 换凭据就清掉上一个凭据的字段路径：名字在另一条凭据里通常不存在
  if (item?.id !== bindingSecretId.value) bindingValuePath.value = ''
}

/** 字段清单加载完：结构化且有字段时，必须选一个才能保存。 */
function onFieldsLoaded(info: { format: string; fields: { name: string }[] }) {
  bindingNeedsField.value = info.format === 'json' && info.fields.length > 0
}

/**
 * 生成这一行该有的值。
 *
 * **一律写 ``${CREDENTIAL:标识}``，取哪一部分交给绑定上的 ``value_path``。**
 *
 * 别改写成 ``${secret:标识.字段}``：那种写法在注入层会校验「引用里的标识 == 本次
 * 解析到的凭据标识」。平台配的是管理员那条凭据，普通用户绑自己的凭据去覆盖时标识
 * 必然对不上，直接 ``reference_mismatch`` ——「个人覆盖平台默认」就废了。
 * ``${CREDENTIAL:...}`` 不参与这项校验，注入时用当前绑定自己的 value_path，
 * 谁绑的就取谁的字段，正是这里需要的行为。
 *
 * 多字段拼接（``Bearer ${secret:k.a}-${secret:k.b}``）仍可手工写：值里带引用时
 * 走引用渲染，不受 value_path 影响。
 *
 * 已有前缀（``Bearer``）保留：用户写的认证方案不该被覆盖。
 */
function buildHeaderValue(original: string, secretKey: string): string {
  const ref = `\${CREDENTIAL:${secretKey}}`
  const text = (original || '').trim()
  if (!text) return ref
  // 已经有指向凭据的占位符：整段换掉，不要叠加
  if (/\$\{(?:secret|CREDENTIAL):[^}]*\}/i.test(text)) {
    return text.replace(/\$\{(?:secret|CREDENTIAL):[^}]*\}/gi, ref)
  }
  // 手填过前缀（Bearer / Token …）：只替换「值」那一截，保留认证方案
  const spaceAt = text.indexOf(' ')
  if (spaceAt > 0) return `${text.slice(0, spaceAt)} ${ref}`
  return ref
}

async function submitBinding() {
  if (!bindingSlot.value) return
  const picked = bindingSecretId.value ? pickedItem.value : null
  // 结构化凭据没选字段就保存，注进去的是一整段 JSON——调用方必错，这里拦住
  if (picked && bindingNeedsField.value && !bindingValuePath.value) {
    bindingError.value = '这条凭据是结构化的，请先选择要取用的字段'
    return
  }
  const valuePath = picked ? bindingValuePath.value : ''
  bindingBusy.value = true
  bindingError.value = ''
  const slot = bindingSlot.value
  const headerName = bindingSlotName.value

  // 新建态：工具还没有 id，先记下来，保存后由父组件调 flushPending() 补写
  if (!props.consumerId) {
    if (picked) {
      pendingBindings.value[slot] = {
        secret_id: picked.id,
        secret_key: picked.secret_key,
        secret_name: '',
        value_path: valuePath,
        owner_scope: picked.owner_scope,
        value_format: picked.value_format,
        value_template: picked.value_template,
      }
    } else {
      delete pendingBindings.value[slot]
    }
    const row = rows.value.find((r) => r.key.trim() === headerName)
    if (row) {
      // 选凭据就写占位符、取消就清空：占位符让配置里留着一个「这里由凭据填」
      // 的名字，注入层才找得到这个 header；取消时清空才回到「没配」。
      row.value = picked ? buildHeaderValue(row.value, picked.secret_key) : ''
      emitFromRows()
    }
    bindingSlot.value = null
    bindingSecretId.value = null
    bindingBusy.value = false
    return
  }

  try {
    if (!picked) {
      await axios.delete('/api/portal/secrets/bindings', {
        data: {
          consumer_type: props.consumerType,
          consumer_id: props.consumerId,
          slot,
          is_global: props.isGlobal,
        },
      })
      delete bindings.value[slot]
      const row = rows.value.find((r) => r.key.trim() === headerName)
      if (row) {
        row.value = ''
        emitFromRows()
      }
    } else {
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: picked.id,
        consumer_type: props.consumerType,
        consumer_id: props.consumerId,
        slot,
        // 作用域由消费端声明（见 isGlobal 的说明），组件不猜。
        is_global: props.isGlobal,
        // 绑定记录里也存一份：万一用户后来手改了值里的引用，注入层仍知道取哪个字段
        value_path: valuePath,
      })
      bindings.value[slot] = {
        secret_id: picked.id,
        secret_key: picked.secret_key,
        secret_name: '',
        value_path: valuePath,
        // 与 pendingBindings 同口径：不带的话，刚绑完重开弹窗提示就没了
        owner_scope: picked.owner_scope,
        value_format: picked.value_format,
        value_template: picked.value_template,
      }
      const row = rows.value.find((r) => r.key.trim() === headerName)
      if (row) {
        row.value = buildHeaderValue(row.value, picked.secret_key)
        emitFromRows()
      }
    }
    bindingSlot.value = null
    bindingSecretId.value = null
    await loadBindings()
  } catch (e: any) {
    bindingError.value = e?.response?.data?.detail || '保存绑定失败'
  } finally {
    bindingBusy.value = false
  }
}


/**
 * 保存工具后用真实 id 补写暂存的绑定，返回失败的槽位（调用方决定怎么提示）。
 *
 * 工具已经建好了，绑定失败不该让它回滚——但必须说出来，否则界面显示「已绑定」，
 * 实际调用拿不到值。
 */
async function flushPending(consumerId: string) {
  const entries = Object.entries(pendingBindings.value)
  if (!entries.length) return [] as string[]
  const failed: string[] = []
  const keep: Record<string, BindingInfo> = {}
  for (const [slot, binding] of entries) {
    try {
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: binding.secret_id,
        consumer_type: props.consumerType,
        consumer_id: consumerId,
        slot,
        is_global: props.isGlobal,
        value_path: binding.value_path || '',
      })
    } catch {
      failed.push(slot)
      keep[slot] = binding
    }
  }
  // 失败的留在暂存里：清掉的话「哪些没绑上」就查不到了
  pendingBindings.value = keep
  return failed
}

defineExpose({
  flushPending,
  hasPendingBindings: () => Object.keys(pendingBindings.value).length > 0,
})

watch(
  () => [props.consumerId, props.consumerType],
  () => void loadBindings(),
  { immediate: true },
)

watch(
  () => props.modelValue,
  (value) => {
    if (syncing) return
    if (mode.value === 'json') {
      parseHeaders(value)
      return
    }
    const parsed = parseHeaders(value)
    if (!parsed) return
    const current = rowsToJson()
    let incoming = ''
    try {
      incoming = JSON.stringify(parsed, null, 2)
    } catch {
      incoming = ''
    }
    if (current !== incoming) syncRows(value)
  },
  { immediate: true },
)
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <label class="block text-sm font-medium text-gray-700">Headers</label>
      <div class="flex rounded-lg bg-gray-100 p-0.5 text-[11px]" role="group" aria-label="Headers 编辑方式">
        <button
          type="button"
          class="rounded-md px-2 py-1 font-medium transition-colors"
          :class="mode === 'rows' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="switchToRows"
        >键值对</button>
        <button
          type="button"
          class="rounded-md px-2 py-1 font-medium transition-colors"
          :class="mode === 'json' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="switchToJson"
        >JSON 高级</button>
      </div>
    </div>

    <template v-if="mode === 'rows'">
      <div v-if="rows.length" class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_1.75rem_2rem] gap-1.5 px-1 text-[11px] text-gray-400 sm:grid">
        <span>名称</span>
        <span>值</span>
        <span class="text-center" title="绑定凭据中心里的凭据">凭据</span>
        <span></span>
      </div>
      <div
        v-for="row in rows"
        :key="row.id"
        class="grid grid-cols-[minmax(0,1fr)_2rem] gap-1.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_1.75rem_2rem] sm:items-center"
      >
        <input
          :ref="(el) => { if (el) inputRefs.set(row.id, el as HTMLInputElement) }"
          :value="row.key"
          class="min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          placeholder="Header 名称"
          aria-label="Header 名称"
          @input="onKeyInput(row, $event)"
        />
        <input
          :value="row.value"
          class="col-span-2 min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 sm:col-span-1"
          placeholder="值，支持 {user.user_name} 等用户变量"
          aria-label="Header 值"
          @input="onValueInput(row, $event)"
        />
        <button
          type="button"
          :disabled="!row.key.trim()"
          class="col-span-2 justify-self-start rounded-md p-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-30 sm:col-span-1 sm:justify-self-center"
          :class="bindingIssue(bindingOf(row.key))
            ? 'text-amber-600 hover:text-amber-700'
            : bindingOf(row.key) ? 'text-emerald-600 hover:text-emerald-700' : 'text-gray-400 hover:text-primary'"
          :title="!row.key.trim()
            ? '先填 Header 名称'
            : bindingIssue(bindingOf(row.key))
              ? `已绑定 ${bindingOf(row.key)?.secret_key}，但${bindingIssue(bindingOf(row.key))}`
              : bindingOf(row.key) ? `已绑定 ${bindingOf(row.key)?.secret_key}` : '绑定凭据中心的凭据'"
          :aria-label="`为 Header ${row.key || '未命名'} 绑定凭据`"
          @click="openBinding(row)"
        >
          <KeyIcon class="h-4 w-4" />
        </button>
        <button
          type="button"
          class="col-span-2 justify-self-end rounded-md p-1.5 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 sm:col-span-1 sm:justify-self-center"
          :aria-label="`删除 Header ${row.key || '未命名'}`"
          @click="removeRow(row.id)"
        >
          <TrashIcon class="h-4 w-4" />
        </button>
      </div>
      <p v-if="rows.length === 0" class="rounded-md border border-dashed border-gray-200 bg-gray-50 px-2.5 py-2 text-xs text-gray-400">
        还没有 Header；大多数接口不需要额外 Header。
      </p>
      <button
        type="button"
        class="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        @click="addRow"
      >+ 添加 Header</button>

    </template>

    <textarea
      v-else
      :value="modelValue"
      rows="4"
      class="block w-full rounded-md border border-gray-300 bg-white px-2.5 py-2 font-mono text-xs shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
      placeholder='{ "Authorization": "Bearer token" }'
      aria-label="Headers JSON"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    ></textarea>

    <!-- 平台工具：绑定只属于当前用户，别说成「给全平台配好了」。
         这里只讲清「谁生效、想给所有人用该怎么办」，不指路——这个页面是管理员
         的系统配置页，让人去「个人中心 → 我的 MCP」纯属指错方向。 -->
    <div class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-800">
      这是<strong>平台工具</strong>：请求头和签名配置全平台共用，但<strong>凭据绑定是个人级</strong>的——
      你绑的只对你自己的调用生效。想让所有人共用一个 Token，直接手填（注入层对手填值只补空位）。
    </div>

    <p v-if="displayError" class="text-[11px] text-red-600">{{ displayError }}</p>
    <p v-else class="text-[11px] text-gray-400">
      敏感值建议用变量占位（如 <code>{user.user_name}</code>）——调用时才替换；
      直接填明文会原样写进工具配置。点右侧钥匙图标可改用凭据中心的凭据，
      值会变成占位符（结构化凭据可精确到某个字段），不写入配置。
    </p>
  </div>
  <!-- 绑定凭据 -->
  <Teleport to="body">
      <div
        v-if="bindingSlot"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="bindingSlot = null"
      >
        <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
          <h3 class="text-base font-semibold text-gray-900">
            为 <code class="rounded bg-gray-100 px-1.5 py-0.5 text-sm">{{ bindingSlot }}</code> 绑定凭据
          </h3>
          <p class="mt-1 text-xs leading-relaxed text-gray-500">
            选中后这一行的值会变成占位符 <code>${CREDENTIAL:标识}</code>，原有的认证方案前缀
            （<code>Bearer</code> / <code>Basic</code>）保留。
            结构化凭据取哪个字段由绑定上的「取用字段」决定，不再写进值里——
            值里带字段名的话，普通用户绑自己的凭据覆盖时会因为标识对不上直接失败。
            真实值不写进工具配置，调用时由注入层现取——所以工具被导出、复制、进审计都不会带走密钥。
          </p>

          <div class="mt-3 space-y-2">
            <CredentialPicker v-model="bindingSecretId" scene="api_tool" @picked="onPicked" />
            <!-- 结构化凭据必须落到具体字段：整段 JSON 注进去，调用方一定收不对 -->
            <SecretFieldSelect
              v-if="bindingSecretId"
              v-model="bindingValuePath"
              :secret-id="bindingSecretId"
              @loaded="onFieldsLoaded"
            />
          </div>

          <p
            v-if="personalScopeNotice && !bindingError"
            class="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-700"
          >{{ personalScopeNotice }}</p>
          <p v-if="bindingError" class="mt-2 text-[11px] text-red-600">{{ bindingError }}</p>
          <p v-else-if="currentBinding" class="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-emerald-600">
            当前已绑定
            <SecretBindingLabel
              :secret-key="currentBinding.secret_key"
              :format="currentBinding.value_format"
              :template="currentBinding.value_template"
              :value-path="currentBinding.value_path"
            />
          </p>

          <div class="mt-4 flex justify-end gap-2">
            <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="bindingSlot = null">
              取消
            </button>
            <button
              type="button"
              :disabled="bindingBusy"
              class="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              @click="submitBinding"
            >
              {{ bindingBusy ? '保存中…' : bindingSecretId ? '保存绑定' : '清除绑定' }}
            </button>
          </div>
        </div>
      </div>
  </Teleport>
</template>
