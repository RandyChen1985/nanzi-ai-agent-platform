<script setup lang="ts">
/**
 * 「多值凭据」页：一条凭据（或一条挑凭据的规则）供多个参数。
 *
 * 它不是新存储：参数 A 取字段 x 本来就是一条绑定（同一个 secret_id / 同一条规则，
 * 不同的 value_path）。所以这里配完，单值页也能看见、也能改——两页是同一批记录的
 * 两种看法。
 *
 * 保存语义是**整页覆盖**：选中的参数绑凭据，没选的一律回到「模型填写」。
 * 「这张表就是这个工具的参数配置」比「只改动过的」更符合这里的心理模型。
 */
import { computed, ref, watch } from 'vue'
import { useConfirm } from '@/composables/useConfirm'
import axios from '@/utils/axios'
import type { CredentialPickerItem } from '@/utils/credentialPicker'
import { CREDENTIAL_TEMPLATES, findCredentialTemplate } from '@/utils/credentialTemplates'
import SecretLocatorEditor, { type LocatorRule } from './SecretLocatorEditor.vue'

const { confirm } = useConfirm()

const props = withDefaults(
  defineProps<{
    consumerType: string
    consumerId: string
    /** 管理员给全平台配（写入全局绑定） */
    isGlobal?: boolean
    /** 这次能映射的参数名 */
    params: string[]
    /** 参数名 → 槽位。不给就让后端按工具的 URL/方法推算，MCP 必须给 */
    slots?: Record<string, string>
    /** 单值页里已经配了凭据的参数：本页保存会把它们改成这里的样子 */
    singleValueParams?: Record<string, string>
    subject?: string
    /**
     * MCP 工具 id。带上它，后端会在同一个事务里把「由凭据提供」的参数清单
     * （credential_params）也更新掉。
     *
     * 不带的话就得由父组件在保存成功后再发一次 PUT——那条路一旦失败，绑定已经在
     * 库里、清单却是空的，运行时模型照旧自己填密码，界面上还一切正常。
     */
    mcpToolId?: string
  }>(),
  { isGlobal: false, slots: () => ({}), singleValueParams: () => ({}), subject: '', mcpToolId: '' },
)

const emit = defineEmits<{
  (
    e: 'saved',
    payload: {
      saved: number
      removed: number
      /** 这次存的是固定凭据还是匹配规则，调用方据此标注参数来源 */
      source: 'fixed' | 'rule'
      bound: string[]
      freed: string[]
      /** 后端同批写回的「由凭据提供」清单；没带 mcpToolId 时为 null */
      credentialParams: string[] | null
    },
  ): void
}>()

interface FieldMeta {
  name: string
  sensitive: boolean
  preview: string
}

const source = ref<'fixed' | 'rule'>('fixed')
const secrets = ref<CredentialPickerItem[]>([])
const secretId = ref('')
const liveFields = ref<FieldMeta[]>([])
const liveFormat = ref('text')
const picks = ref<Record<string, string>>({})
const original = ref<Record<string, string>>({})
/** 被别的凭据或规则占着的参数（参数名 → 来源名） */
const takenByOther = ref<Record<string, string>>({})
const ruleDraft = ref<LocatorRule | null>(null)
/**
 * 用户有没有动过「来源」或「规则」。字段映射的改动可以直接拿 ``picks`` 和
 * ``original`` 比，但「把来源从固定凭据切成按规则」「改了一条规则」并不一定
 * 改变已选字段，光比 ``picks`` 会漏掉——切换标签页时就可能把这类改动悄悄丢掉。
 */
const touched = ref(false)
const ruleOpen = ref(false)
const busy = ref(false)
const errorText = ref('')
const autoHint = ref('')

const structured = computed(() =>
  secrets.value.filter((s) => (s.value_format || 'text') === 'json'),
)

const TEMPLATE_OPTIONS = CREDENTIAL_TEMPLATES.map((t) => ({ value: t.id, label: `${t.name} · ${t.id}` }))

/**
 * 改「凭证内容模板」就是改字段集合——这件事发生在看映射表的时候，而不是配规则的时候。
 *
 * 原来它只在规则弹窗的第⑤步：那时用户还没看到有哪些参数要映射，选模板等于
 * 盲选一个自己不知道影响什么的选项。移到映射表标题行后，「选模板 → 字段下拉
 * 随之确定」这个因果关系就在同一屏里。
 *
 * 写的是同一份 `ruleDraft`，所以点「保存映射」时规则会跟着一起提交
 * （items 里 strategy='rule' 带 ruleBody），不需要额外再存一次规则。
 */
const ruleTemplate = computed({
  get: () => ruleDraft.value?.value_template || '',
  set: (value: string) => {
    if (!ruleDraft.value) return
    const before = ruleDraft.value.value_template || ''
    ruleDraft.value.value_template = value
    if (before === value) return
    // 选「不限定」不是「换了个空模板」：它的语义是「字段放开手填」，现有值全部
    // 仍然合法。这里如果照下面那套过滤走，`allowed` 是空集，会把用户已经填好的
    // 字段全部删光——`onRuleSaved` 里特意防过同一个坑，两处必须一致。
    if (!value) {
      touched.value = true
      return
    }
    // 换了模板 = 字段集合变了。旧的下拉值可能在新集合里不存在（smtp.host 之于
    // 另一种结构），留着会提交一个取不到的字段名，运行时报「字段不存在」，
    // 而界面上那一行看起来是选好的。
    const allowed = new Set(
      (findCredentialTemplate(value)?.fields || []).map((f) => f.key),
    )
    for (const param of Object.keys(picks.value)) {
      if (picks.value[param] && !allowed.has(picks.value[param])) delete picks.value[param]
    }
    touched.value = true
  },
})

/** 规则模式下字段集来自模板约束：没有它，字段名无从得知，只能靠手输 */
const ruleFields = computed<FieldMeta[]>(() => {
  const tpl = findCredentialTemplate(ruleDraft.value?.value_template)
  if (!tpl) return []
  return tpl.fields.map((f) => ({
    name: f.key,
    sensitive: Boolean(f.secret),
    preview: '',
  }))
})

const candidateFields = computed(() =>
  source.value === 'rule' ? ruleFields.value : liveFields.value,
)

/**
 * 还没选凭据 / 还没配规则。
 *
 * 这时字段候选必然是空的，但**不该**因此退回手填框：多值配法的顺序是先有
 * 一条凭据（或一条规则）才谈得上「把它的字段分给各参数」，直接给个空输入框
 * 等于让用户凭空猜字段名。真正需要手填的是「不限定结构」的规则（模板没约束
 * 字段集），那种情况 ruleDraft 存在，走不到这里。
 */
const needsSourceFirst = computed(() =>
  source.value === 'rule' ? !ruleDraft.value : !secretId.value,
)

/** 「让模型挑」的规则没有取值来源，判定与 SecretLocatorEditor 的 mode 同口径 */
const ruleIsModelChoice = computed(
  () => !!ruleDraft.value && !ruleDraft.value.key_source && ruleDraft.value.on_miss === 'model',
)

const ruleSummaryText = computed(() => {
  const rule = ruleDraft.value
  if (!rule) return ''
  // 按字段值匹配时要说清比的是哪个字段：只显示「匹配凭据的 field」等于没说
  const targetLabel =
    rule.target === 'field'
      ? `字段 ${rule.target_field || '?'}`
      : rule.target || '?'
  if (ruleIsModelChoice.value) {
    return rule.value_template ? `模板 ${rule.value_template}` : '不限模板'
  }
  const parts = [
    `按 ${rule.key_source || '?'} 匹配凭据的 ${targetLabel}`,
    rule.match === 'exact' ? '精确' : rule.match || '',
    rule.value_template ? `模板 ${rule.value_template}` : '不限模板',
  ]
  return parts.filter(Boolean).join(' · ')
})

/**
 * 「挑不到时」的策略。
 *
 * 这一条决定的是**失败形态**：直接报错、回退到固定凭据、还是把选择权交给
 * 模型。它不进 ``ruleSummaryText``（那条说的是「怎么挑」），单独一行显示，
 * 因为它才是用户排查「为什么这次调用挂了」时最需要看见的一句。
 */
const ruleMissText = computed(() => {
  switch (ruleDraft.value?.on_miss) {
    case 'model':
      return '让模型在可见候选里挑一条'
    case 'fallback_fixed':
      return '回退到同一层级的固定凭据'
    default:
      return '直接报错，这次调用失败'
  }
})

/** 单值页已配、本页没选的参数：保存会把它们改回模型填写，要提醒 */
const takeover = computed(() =>
  props.params.filter((name) => props.singleValueParams?.[name] && !picks.value[name]),
)

const pendingChanges = computed(() => {
  const changed: { name: string; valuePath: string }[] = []
  for (const name of props.params) {
    const now = picks.value[name] || ''
    const before = original.value[name] || ''
    if (now !== before) changed.push({ name, valuePath: now })
  }
  return changed
})

/**
 * 需要点保存才会生效的改动数。
 *
 * ``pendingChanges`` 只比字段映射：改了来源或规则、但字段恰好一个没动时它是空的。
 * 那种情况同样要保存（运行时读的是规则本身），不提示的话用户关掉弹窗就以为生效了。
 */
/**
 * 已经选了字段的参数个数。
 *
 * 0 个时「保存映射」不该可点：那时提交上去等于把整组绑定都删掉（全部回到
 * 模型填写），而这件事有专门的「清空全部」——它带确认框、文案也说得明白。
 * 让「保存」在这个状态下静默清空，是配错一次就丢一整组配置的入口。
 */
const mappedCount = computed(() => props.params.filter((n) => picks.value[n]).length)

const pendingChangeCount = computed(() =>
  pendingChanges.value.length || (touched.value ? 1 : 0)
)

/** 参数 → 槽位。MCP 必须给（``arg:短工具名:参数``），API 工具留空由后端推算。 */
function slotOf(name: string): string {
  return props.slots?.[name] || ''
}

/**
 * 从槽位反推参数名。
 *
 * MCP 的槽位是 ``arg:<短工具名>:<参数名>``，只取末段会撞车——同一个服务下
 * 另一个工具也有 ``date`` 时，两个参数会互相串台。所以下面还会再用
 * ``props.slots`` 精确比对一次，这里的末段只用来做初筛。
 */
function nameOfSlot(slot: string): string {
  return String(slot || '').split(':').pop() || ''
}

/** 只取字段清单，不动已经填好的映射 */
async function fetchFields(id: string) {
  liveFields.value = []
  liveFormat.value = 'text'
  if (!id) return
  try {
    const res = await axios.get(`/api/portal/secrets/${id}/fields`)
    const data = res.data?.data
    liveFields.value = data?.fields || []
    liveFormat.value = data?.format || 'text'
  } catch (e: any) {
    errorText.value = e?.response?.data?.detail || '读取凭据字段失败'
  }
}

/**
 * 回填：摸清这个工具现在是怎么配的，再决定默认选哪条凭据。
 *
 * 顺序很重要——先读绑定、后选凭据。反过来（先挑列表里第一条结构化凭据）会让
 * 打开页面变成「换了一条凭据」：已有映射全部清空，用户以为配置丢了。
 */
async function load() {
  busy.value = true
  errorText.value = ''
  try {
    const [pickerRes, bindingsRes] = await Promise.all([
      axios.get('/api/portal/secrets/picker'),
      props.consumerId
        ? axios.get('/api/portal/secrets/bindings', {
            params: { consumer_type: props.consumerType, consumer_id: props.consumerId },
          })
        : Promise.resolve({ data: { data: [] } }),
    ])
    const groups = pickerRes.data?.data?.groups || {}
    secrets.value = [...(groups.mine || []), ...(groups.shared || [])]

    // 认「本人绑的」或「全局的」，不是严格等于本页作用域。
    //
    // 严格相等的写法有两个坑：① 管理员拿自己的个人凭据绑平台级工具时，后端会把
    // 它降级成本人绑定（个人凭据没法给所有人用），`is_global` 是 false，严格比对
    // 会把它当成「没绑过」——刚保存完再打开就显示未绑定；② 管理员在一页里可能
    // 既有全局绑定又有自己的个人绑定，只能显示其中一半。
    const rows = (bindingsRes.data?.data || []).filter(
      (r: any) => r.bound_by_me || Boolean(r.is_global) === Boolean(props.isGlobal),
    )

    // 按 strategy 分开存：同一个参数可以同时挂「规则」和「回退固定目标」两条绑定
    // （on_miss=fallback_fixed），塞进同一个 Map 会互相覆盖，覆盖方向还取决于
    // 接口返回顺序——表现就是「明明配了规则，界面上却显示成固定凭据」。
    const byNameFixed = new Map<string, any>()
    const byNameRule = new Map<string, any>()
    for (const row of rows) {
      const name = nameOfSlot(row.slot)
      if (!props.params.includes(name)) continue
      const expected = slotOf(name)
      if (expected && row.slot !== expected) continue
      if ((row.strategy || 'fixed') === 'rule') byNameRule.set(name, row)
      else byNameFixed.set(name, row)
    }

    const ruleRow = [...byNameRule.values()][0]
    const fixedRows = [...byNameFixed.values()]

    // 一个工具通常由一条凭据供多个参数：取出现次数最多的那条作为本页选中的凭据
    const counts = new Map<string, number>()
    for (const row of fixedRows) {
      if (row.secret_id) counts.set(row.secret_id, (counts.get(row.secret_id) || 0) + 1)
    }
    let boundId = ''
    for (const [id, n] of counts) {
      if (!boundId || n > (counts.get(boundId) || 0)) boundId = id
    }

    const mine: Record<string, string> = {}
    if (ruleRow) {
      // 规则模式：参与映射的参数各有一条同规则、不同 value_path 的绑定
      for (const [name, row] of byNameRule) mine[name] = row.value_path || ''
      ruleDraft.value = ruleRow.rule
        ? { ...ruleRow.rule, value_path: ruleRow.value_path || '' }
        : null
      source.value = 'rule'
    } else {
      for (const [name, row] of byNameFixed) {
        if (row.secret_id && row.secret_id === boundId) mine[name] = row.value_path || ''
      }
      source.value = 'fixed'
    }

    // 被别的凭据占着的参数：不提示的话，用户会以为这行「没配过」，
    // 一保存就被清空
    const taken: Record<string, string> = {}
    for (const name of byNameRule.keys()) {
      if (!mine[name]) taken[name] = '按规则匹配'
    }
    for (const [name, row] of byNameFixed) {
      if (mine[name] || taken[name]) continue
      // 规则模式下 `boundId` 不代表「本页要用的那条固定凭据」（本页存的是规则），
      // 它只是从 fixed 绑定里挑出来出现最多的那个。照旧拿它比 `secret_id`，绑着
      // 其他凭据的参数就不会被标出来——行内显示「模型填写」，而保存是**整页覆盖**，
      // 未选字段的参数一律提交 none，用户看着「模型填写」点保存，实际把那条绑定删了。
      const occupied = source.value === 'rule' || row.secret_id !== boundId
      if (row.secret_id && occupied) {
        taken[name] = row.secret_key || '其他凭据'
      }
    }
    takenByOther.value = taken

    original.value = { ...mine }
    picks.value = { ...mine }
    touched.value = false

    secretId.value = boundId || structured.value[0]?.id || ''
    await fetchFields(secretId.value)
  } catch (e: any) {
    errorText.value = e?.response?.data?.detail || '加载凭据列表失败'
  } finally {
    busy.value = false
  }
}

/**
 * 这一页有没有没保存的改动。
 *
 * 父组件切换「单值 / 多值」标签页时会问它：多值面板是 ``v-if`` 挂载的，切走即销毁，
 * 没保存的映射会无声消失；而单值页的草稿是父组件自己的 ref，切走再切回还在。
 * 两边行为不一致，用户切一次就可能丢配置，所以切换前必须问一句。
 */
const isDirty = computed(() => {
  if (touched.value) return true
  const names = new Set([...Object.keys(picks.value), ...Object.keys(original.value)])
  for (const name of names) {
    if ((picks.value[name] || '') !== (original.value[name] || '')) return true
  }
  return false
})

defineExpose({ isDirty })

function pickSource(next: 'fixed' | 'rule') {
  if (source.value === next) return
  source.value = next
  touched.value = true
}

/** 手动换凭据：字段名属于旧凭据，全部清掉重选 */
async function changeSecret() {
  picks.value = {}
  original.value = {}
  takenByOther.value = {}
  // original 被一起清空了，只比 picks 与 original 的话换完凭据反而判成「没改动」。
  // 换凭据本身就意味着「这一组要重新配字段」，必须算脏。
  touched.value = true
  await fetchFields(secretId.value)
}

function onRuleSaved(rule: LocatorRule) {
  ruleDraft.value = rule
  source.value = 'rule'
  // 必须算脏：新规则的字段集和旧的一样时（只改了 match / key_source / target），
  // 下面的 picks 不会变，只比 picks 的话这里判不出改动——切 tab 或关弹窗时
  // 规则草稿会被静默丢掉，而它恰恰要「保存映射」才生效。
  touched.value = true
  // 模板换了，旧字段名通常不存在了——但只在**真的能列出字段**时才清理。
  // 选「不限定结构」时 ruleFields 是空的，照清不误会把用户手填的字段名全抹掉：
  // 那个场景下没有下拉可选，手填是唯一途径，清掉等于让他重填一遍。
  if (!ruleFields.value.length) {
    ruleOpen.value = false
    return
  }
  for (const name of props.params) {
    if (picks.value[name] && !ruleFields.value.some((f) => f.name === picks.value[name])) {
      picks.value[name] = ''
    }
  }
  ruleOpen.value = false
}

function autoMatch() {
  const names = candidateFields.value.map((f) => f.name)
  let matched = 0
  for (const param of props.params) {
    if (picks.value[param]) continue
    if (names.includes(param)) {
      picks.value[param] = param
      matched += 1
    }
  }
  autoHint.value = matched
    ? `按名字对上了 ${matched} 个，剩下的自己选`
    : '没有参数名和字段名撞上，得手动选'
}

/**
 * 一键清空：把这个工具所有参数的凭据配置（固定凭据与规则）全部清掉。
 *
 * 为什么要单独一条路径而不是「把字段全清空再点保存」：``save`` 有两道守卫
 * （没选凭据 / 没配规则就报错），它们挡的正是「配置残缺时误保存」，而清空
 * 恰恰是主动、完整的动作——硬塞进 save 就得给守卫开后门。
 *
 * 不提「单值/多值」概念：后端 ``strategy='none'`` 会把该槽位的 fixed 与 rule
 * **两条都删掉**，所以对用户来说结果只有一个——参数回到「模型填写」。
 */
async function clearAll() {
  if (!props.consumerId || busy.value) return
  const count = props.params.length
  const ok = await confirm({
    title: '清空全部凭据配置',
    message: `将清除这个工具全部 ${count} 个参数的凭据配置（固定凭据与规则都会删除），参数回到「模型填写」。`,
    confirmText: '清空',
    danger: true,
  })
  if (!ok) return
  busy.value = true
  errorText.value = ''
  try {
    const items = props.params.map((name) => ({
      slot: props.slots?.[name] || '',
      name,
      strategy: 'none' as const,
    }))
    const res = await axios.post('/api/portal/secrets/bindings/batch', {
      consumer_type: props.consumerType,
      consumer_id: props.consumerId,
      is_global: props.isGlobal,
      items,
      // 全部清空没有「组」可言：声明成非多值，免得后端按条目数建一个空组
      as_group: false,
      mcp_tool_id: props.mcpToolId || undefined,
    })
    const data = res.data?.data || { saved: 0, removed: 0 }
    picks.value = {}
    original.value = {}
    ruleDraft.value = null
    source.value = 'fixed'
    touched.value = false
    await load()
    emit('saved', {
      saved: data.saved || 0,
      removed: data.removed || 0,
      source: 'fixed',
      bound: [],
      freed: props.params.slice(),
      credentialParams: Array.isArray(data.credential_params) ? data.credential_params : null,
    })
  } catch (e: any) {
    errorText.value = e?.response?.data?.detail || '清空失败'
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!props.consumerId) {
    errorText.value = '工具还没保存，先把工具存下来再配参数凭据'
    return
  }
  if (source.value === 'rule' && !ruleDraft.value) {
    errorText.value = '还没有配规则'
    return
  }
  // 选了字段却没选凭据：会提交成 strategy=fixed + secret_id=''，后端按
  // 「回到模型填写」处理——绑定和清单一起被清掉，还不报错。
  // 正常操作走不到（换凭据时 changeSecret 会清空 picks），这里是兜底：
  // 让下一个人加交互时不会把这个洞重新打开。
  if (
    source.value === 'fixed' &&
    !secretId.value &&
    // 判值不判 key：切回「模型填写」时 `v-model="picks[name]"` 只把值设成空串，
    // key 还留在对象里。按 key 判的话，一旦选过字段就再也保存不了「全部清空」。
    Object.values(picks.value).some(Boolean)
  ) {
    errorText.value = '还没选凭据，先选一条再把字段分给参数'
    return
  }
  busy.value = true
  errorText.value = ''
  try {
    // 整页覆盖：没选字段的参数提交 none，由后端把固定与规则两条都清掉。
    //
    // `value_path` 不属于规则 JSON（它是绑定记录上的列），所以从 ruleBody 里摘掉。
    // 但这个值**不能**回头当所有行的默认字段：`load()` 会把第一条规则行的路径
    // 回填进来，于是「某个参数绑了 password」会让所有留空（= 模型填写）的参数
    // 在下次保存时都被绑上 password——界面还显示它们已经回到模型填写了。
    const { value_path: _bindingPath, ...ruleBody } = (ruleDraft.value || {}) as LocatorRule
    void _bindingPath
    const items = props.params.map((name) => {
      const path = picks.value[name] || ''
      const base = { slot: props.slots?.[name] || '', name }
      if (!path) return { ...base, strategy: 'none' }
      if (source.value === 'rule') {
        return { ...base, strategy: 'rule', rule: ruleBody, value_path: path }
      }
      return { ...base, strategy: 'fixed', secret_id: secretId.value, value_path: path }
    })
    const res = await axios.post('/api/portal/secrets/bindings/batch', {
      consumer_type: props.consumerType,
      consumer_id: props.consumerId,
      is_global: props.isGlobal,
      items,
      // 这一页的语义就是「一条凭据/一条规则铺 N 个参数」，必须显式声明：
      // 后端不再按条目数猜（MCP 单值凭据页也走这个接口，猜就会把独立绑定锁成一组）
      as_group: true,
      // 清单和绑定必须同批写：分两次请求的话，第一次成功第二次失败就是「绑了却不注入」
      mcp_tool_id: props.mcpToolId || undefined,
    })
    const data = res.data?.data || { saved: 0, removed: 0 }
    // 保存后按库里的事实重算一遍：界面此前显示的是内存草稿。
    // 不重算的话，「保存其实没落成规则」这种状态要等到关掉再打开才暴露，
    // 而那时用户已经以为配好了——排查方向会全错。
    await load()
    original.value = { ...picks.value }
    // 保存成功即成为新基线，这个时候不该再算「有未保存改动」
    touched.value = false
    emit('saved', {
      saved: data.saved || 0,
      removed: data.removed || 0,
      // 必须带上「这次存的是哪种来源」：调用方要据此把参数的 source 标成
      // credential 还是 rule。只报告 bound 名字的话，按规则保存的参数会被标成
      // credential，而它没有固定绑定——父组件立刻把它判成「未绑定」并锁死保存。
      source: source.value,
      bound: props.params.filter((n) => picks.value[n]),
      freed: props.params.filter((n) => !picks.value[n]),
      credentialParams: Array.isArray(data.credential_params) ? data.credential_params : null,
    })
  } catch (e: any) {
    errorText.value = e?.response?.data?.detail || '保存失败'
  } finally {
    busy.value = false
  }
}

watch(() => props.consumerId, () => { void load() }, { immediate: true })
</script>

<template>
  <div>
    <p class="mb-3 rounded-lg bg-primary/5 px-3 py-2 text-[11px] leading-relaxed text-primary-dark">
      <span class="font-semibold">①</span> 定一条凭据（或一条挑凭据的规则）
      → <span class="font-semibold">②</span> 把它的字段分给各参数
      → <span class="font-semibold">③</span> 保存映射。
      <strong>没选字段的参数保存后回到「模型填写」</strong>。
    </p>

    <div v-if="takeover.length" class="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
      这些参数在「单值凭据」页已经配了凭据，本页保存会把它们改回模型填写：
      <code v-for="name in takeover" :key="name" class="ml-1 font-mono">{{ name }}</code>
    </div>

    <!-- 凭据来源 -->
    <div class="mb-3 flex gap-2">
      <button
        type="button"
        class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors"
        :class="source === 'fixed' ? 'border-primary bg-primary/5 ring-2 ring-primary/10' : 'border-gray-300 bg-white hover:border-gray-400'"
        @click="pickSource('fixed')"
      >
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-900">
          固定凭据
          <span class="rounded bg-primary/5 px-1 py-0.5 text-[10px] font-medium text-primary-dark">仅结构化</span>
        </div>
        <div class="mt-0.5 text-[11px] text-gray-600">选一条结构化凭据，它的字段就是下面的候选</div>
      </button>
      <button
        type="button"
        class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors"
        :class="source === 'rule' ? 'border-primary bg-primary/5 ring-2 ring-primary/10' : 'border-gray-300 bg-white hover:border-gray-400'"
        @click="pickSource('rule')"
      >
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-900">
          按规则匹配
          <span class="rounded bg-primary/5 px-1 py-0.5 text-[10px] font-medium text-primary-dark">按身份挑</span>
        </div>
        <div class="mt-0.5 text-[11px] text-gray-600">配一条规则决定用谁的凭据，凭证内容模板决定字段集</div>
      </button>
    </div>

    <!-- 固定凭据 -->
    <label v-if="source === 'fixed'" class="mb-3 block">
      <span class="mb-1 block text-[11px] font-semibold text-gray-700">选一条结构化凭据</span>
      <select
        v-model="secretId"
        class="w-full rounded-md border-gray-300 text-xs shadow-sm focus:border-primary focus:ring-primary"
        :disabled="!structured.length"
        @change="changeSecret"
      >
        <option v-if="!structured.length" value="">
          还没有结构化凭据——先去凭据中心把邮箱/数据库这类存成结构化
        </option>
        <option v-for="s in structured" :key="s.id" :value="s.id">
          {{ s.name }} · {{ s.secret_key }} · {{ s.value_template || 'json' }}
        </option>
      </select>
    </label>

    <!-- 按规则 -->
    <div v-else class="mb-3 rounded-lg border border-gray-200 bg-white">
      <div class="flex items-center gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2">
        <span class="text-xs font-semibold text-gray-800">挑凭据的规则</span>
        <div class="ml-auto">
          <button
            type="button"
            class="rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-50"
            @click="ruleOpen = true"
          >{{ ruleDraft ? '改规则' : '配规则' }}</button>
        </div>
      </div>
      <div class="px-3 py-2.5 text-[11px] leading-relaxed">
        <p v-if="!ruleDraft" class="text-amber-700">
          还没配规则。规则和单值页用的是同一条，只是这里还要给每个参数指定取哪个字段。
        </p>
        <template v-else>
          <p class="text-gray-700">
            <span class="mr-1 rounded bg-primary/5 px-1 py-0.5 font-medium text-primary-dark">{{ ruleIsModelChoice ? '配法' : '规则' }}</span>
            {{ ruleSummaryText }}
          </p>
          <p v-if="ruleIsModelChoice" class="mt-1 text-primary-dark">
            <span class="mr-1 rounded bg-primary/5 px-1 py-0.5 font-medium text-primary-dark">怎么挑</span>
            由模型在可见凭据里挑一条（没有精确匹配规则，确定性低于按字段匹配）
          </p>
          <p v-else class="mt-1 text-gray-600">
            <span class="mr-1 rounded bg-amber-50 px-1 py-0.5 font-medium text-amber-700">挑不到时</span>
            {{ ruleMissText }}
          </p>
          <p class="mt-1 text-gray-600">
            <span class="mr-1 rounded bg-primary/5 px-1 py-0.5 font-medium text-primary-dark">取用字段</span>
            每条参数各自指定（见下表）
          </p>
          <p v-if="!ruleDraft.value_template" class="mt-2 rounded bg-amber-50 px-2 py-1.5 text-amber-800">
            还没选凭证内容模板，字段名无从得知，下表只能靠手输。
            在下面「参数 → 字段」标题行里选一个模板，字段就能下拉。
          </p>
        </template>
      </div>
    </div>

    <!-- 字段来源提示 -->
    <p v-if="source === 'fixed' && liveFormat === 'json' && !liveFields.length" class="mb-2 text-[11px] text-rose-600">
      这条凭据读不出字段（值不是 JSON 对象？）
    </p>

    <p class="mb-2 text-[11px] leading-relaxed text-gray-500">
      保存后：<strong class="font-medium text-gray-700">{{ mappedCount }}</strong> 个参数由凭据提供，
      {{ params.filter((n) => !picks[n]).length }} 个回到模型填写
      <template v-if="pendingChangeCount">
        <span class="font-medium text-amber-600">
          · 有 {{ pendingChangeCount }} 处未保存<template v-if="!mappedCount">（还没给参数选字段，选一个才能保存）</template>
        </span>
      </template>
    </p>

    <!-- 映射表 -->
    <div class="rounded-lg border border-gray-200">
      <div class="flex items-center gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2">
        <span class="text-xs font-semibold text-gray-800">
          <span class="mr-1 text-gray-400">②</span>参数 → 字段
        </span>
        <!-- 模板决定「有哪些字段可选」，放在这里才和下面的字段下拉形成因果关系。
             固定凭据的字段来自凭据本身，不需要也不允许在这里换模板。 -->
        <label
          v-if="source === 'rule'"
          class="ml-3 flex min-w-0 items-center gap-1.5 text-[11px] text-gray-500"
          title="声明「命中的凭据都长这样」：选了模板，下面每一行的字段下拉就有了确定的候选；模板不符的凭据按未命中处理"
        >
          <span class="shrink-0">凭证内容模板</span>
          <select
            v-model="ruleTemplate"
            :disabled="!ruleDraft"
            :title="ruleDraft ? '' : '先在上面加一条规则，模板要写在规则上'"
            class="min-w-0 rounded-md border-gray-300 py-0.5 text-[11px] shadow-sm focus:border-primary focus:ring-primary disabled:bg-gray-50 disabled:text-gray-400"
          >
            <option value="">不限定（字段只能手填）</option>
            <option v-for="t in TEMPLATE_OPTIONS" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
        </label>
        <div class="ml-auto flex shrink-0 items-center gap-2">
          <span class="text-[11px] text-gray-500">
            已映射 {{ mappedCount }} / {{ params.length }}
          </span>
          <button
            type="button"
            class="shrink-0 rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-700 hover:bg-gray-50"
            @click="autoMatch"
          >按名字自动匹配</button>
        </div>
      </div>

      <table class="w-full text-left text-xs">
        <thead class="text-[11px] text-gray-500">
          <tr>
            <th class="px-3 py-1.5 font-medium">参数</th>
            <th class="px-3 py-1.5 font-medium">取哪个字段</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="name in params" :key="name" :class="picks[name] ? 'bg-primary/5' : ''">
            <td class="px-3 py-1.5">
              <code class="font-mono text-gray-800">{{ name }}</code>
              <!-- 来源徽标：这个参数的值到底谁给。
                   凭据参数已经从模型可见的 schema 里移除了，但界面上看不出来，
                   用户只能靠猜「这个要模型填还是要配凭据」。 -->
              <span
                v-if="picks[name]"
                class="ml-1.5 rounded bg-emerald-50 px-1 py-0.5 text-[10px] font-medium text-emerald-700"
                title="运行时由平台解密并注入，模型看不到这个值"
              >服务端注入</span>
              <span
                v-else-if="takenByOther[name]"
                class="ml-1.5 rounded bg-amber-50 px-1 py-0.5 text-[10px] font-medium text-amber-700"
              >现由 {{ takenByOther[name] }} 提供</span>
              <span
                v-else
                class="ml-1.5 rounded bg-gray-100 px-1 py-0.5 text-[10px] font-medium text-gray-600"
                title="模型可见，由模型在调用时自行填写"
              >模型填写</span>
            </td>
            <td class="px-3 py-1.5">
              <select
                v-if="candidateFields.length"
                v-model="picks[name]"
                class="w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-primary"
              >
                <option value="">模型填写</option>
                <option v-for="f in candidateFields" :key="f.name" :value="f.name">
                  {{ f.name }}{{ f.sensitive ? '（敏感）' : '' }}
                </option>
              </select>
              <span
                v-else-if="needsSourceFirst"
                class="text-[11px] text-gray-400"
              >先在上面{{ source === 'rule' ? '配一条规则' : '选一条凭据' }}，这里会列出可选字段</span>
              <input
                v-else
                v-model="picks[name]"
                placeholder="字段名（点分隔可取嵌套）"
                class="w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-primary"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="autoHint" class="mt-2 text-[11px] text-gray-500">{{ autoHint }}</p>
    <p v-if="errorText" class="mt-2 text-[11px] text-rose-600">{{ errorText }}</p>

    <!-- 底栏只放按钮。状态说明原来挤在按钮左边，一长就把「清空全部」和
         「保存映射（N 处改动）」压成两行；挪到表格上方后既有整行宽度，
         也不用跟按钮抢横向空间。 -->
    <div class="mt-3 flex items-center justify-end gap-2">
      <!-- 一键清空：配错时逐个改回「模型填写」太麻烦，且容易漏掉某个参数 -->
      <button
        type="button"
        class="shrink-0 whitespace-nowrap rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-sm font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-40"
        :disabled="busy || !params.length"
        title="清除这个工具所有参数的凭据配置（固定凭据与规则都会删除）"
        @click="clearAll"
      >清空全部</button>
      <button
        type="button"
        class="shrink-0 whitespace-nowrap rounded-lg px-4 py-1.5 text-sm font-semibold text-white hover:bg-primary-dark disabled:opacity-50"
        :class="[
          pendingChangeCount ? 'ring-2 ring-amber-400 ring-offset-1' : '',
          mappedCount ? 'bg-primary' : 'bg-primary/40',
        ]"
        :disabled="busy || !params.length || !mappedCount"
        :title="mappedCount
          ? ''
          : '一个参数都没选字段。保存等于把整组凭据配置清空——要清空请用「清空全部」，它带确认。'"
        @click="save"
      >{{ busy
        ? '保存中…'
        : (pendingChangeCount ? `保存映射（${pendingChangeCount} 处改动）` : '保存映射') }}</button>
    </div>

    <!--
      「凭证内容模板」和「取用字段」都不在这里：
      · 模板移到映射表标题行。它决定字段集合，而用户是在看映射表的时候才需要
        决定字段集合；留在弹窗里等于让人在看不到参数的情况下盲选。两处都摆会让
        人怀疑是不是两份配置（其实是同一份 ruleDraft）。
      · 取用字段**不能**在弹窗里。规则是共享的一条，而「取哪一段」是每个参数各自的，
        所以它只能在下表逐行选。放进弹窗就得有个落地点，而任何「兜底填给所有行」
        的做法都会误伤：留空的行本来是「模型填写」，会被悄悄绑上这个字段。
    -->
    <SecretLocatorEditor
      :open="ruleOpen"
      :consumer-type="consumerType"
      :rule="ruleDraft"
      slot=""
      :arg-options="params"
      :title="subject ? `凭据匹配规则 · ${subject}` : '凭据匹配规则'"
      :show-value-path="false"
      :show-value-template="false"
      @update:open="(value: boolean) => { if (!value) ruleOpen = false }"
      @save="onRuleSaved"
    />
  </div>
</template>
