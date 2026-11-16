<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { KeyIcon } from '@heroicons/vue/24/outline'
import axios from '../../utils/axios'
import { useConfirm } from '@/composables/useConfirm'
import PermissionVariableMenu from '../metadata/PermissionVariableMenu.vue'
import CredentialPicker from '../secrets/CredentialPicker.vue'
import SecretFieldSelect from '../secrets/SecretFieldSelect.vue'
import type { CredentialPickerItem } from '@/utils/credentialPicker'
import SecretBindingLabel from '../secrets/SecretBindingLabel.vue'
import SecretFieldMappingPanel from '../secrets/SecretFieldMappingPanel.vue'
import SecretLocatorEditor, { ruleSummary, type LocatorRule } from '../secrets/SecretLocatorEditor.vue'

/** 把绑定上的取用字段并回规则草稿：编辑已有规则时不能把它弄丢。 */
function withRulePath(rule: LocatorRule | null, valuePath?: string | null): LocatorRule | null {
  if (!rule) return null
  return { ...rule, value_path: valuePath || '' }
}
import { SYSTEM_PARAM_SOURCES, SYSTEM_PARAM_TYPES, type SystemParam } from '../../utils/toolSchema'
import type { UserVariable } from '../../utils/userVariables'

interface BoundSecret {
  secret_id: string
  secret_key: string
  /** 绑定还在，但凭据可能已被停用/过期 —— 界面要标出来 */
  secret_status?: string
  /** 只取凭据的某个字段时给路径；空串 = 整段。结构化凭据必须给 */
  value_path?: string
  /** text | json —— 列表里要标出「结构化 · 模板名」 */
  value_format?: string
  value_template?: string | null
  /** user | system：重开弹窗时判断「是不是只对我生效」 */
  owner_scope?: string
  /** ``fixed`` / ``rule``：同槽位两条记录，各自独立 */
  strategy?: string
  rule?: LocatorRule | null
}

const bindingIssue = (binding: BoundSecret | null): string => {
  if (!binding || !binding.secret_status || binding.secret_status === 'active') return ''
  if (binding.secret_status === 'disabled') return '凭据已停用'
  if (binding.secret_status === 'expired') return '凭据已过期'
  return '凭据不可用'
}

const { confirm } = useConfirm()

const props = withDefaults(defineProps<{
    /**
     * 这个消费者是不是平台级（配一次给所有人用）。
     *
     * 消费端（`ToolRegistry` 的系统工具管理页）才知道答案，所以由它传；
     * 组件内部写死 `true` 有一天会变成 bug——真出现「个人级 API 工具」时，
     * 个人工具上的绑定会被误设成全局。
     */
    isGlobal?: boolean
  modelValue: SystemParam[]
  builtinVariables: UserVariable[]
  extraDataVariables: UserVariable[]
  /** 工具 id；为空表示还没保存，绑定先记账、保存后补写 */
  consumerId?: string
  consumerType?: string
  /**
   * 模型可见的普通参数名。
   * 规则的入参键只能从这里取——**绝不能取本组件自己的 modelValue**：
   * 那是系统级参数（本身就是凭据位的），拿它当键会形成「凭据依赖凭据」的环，
   * 而真正该选的业务入参（`applicant`/`to` 之类）反而选不到。
   */
  plainParams?: string[]
}>(), {
  // 默认 true：现有调用方都是平台级。真出现个人级工具时调用方传 false，
  // 组件不用改。
  isGlobal: true,
  consumerId: '',
  consumerType: 'api_tool',
  plainParams: () => [],
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: SystemParam[]): void
  /** 标了「来自凭据」但还没选凭据的参数名，父组件据此拦住保存 */
  (e: 'unbound-changed', names: string[]): void
}>()

const openMenuIndex = ref<number | null>(null)

// ── 凭据绑定 ────────────────────────────────────────────────────────
// 槽位由后端推（path / query / body 取决于 URL 和方法），这里只认参数名。
// 由 ToolRegistry 弹窗提供的共享草稿：向导模式与表单模式共用同一份新建态 pending，
// 切换填写方式时绑定不会丢失，也不会因各自维护一份状态而互相锁住保存按钮。
const injectedDraft = inject<{
  bindings: Ref<Record<string, BoundSecret>>
  rules: Ref<Record<string, LocatorRule>>
  revision?: Ref<number>
  bumpRevision?: () => void
} | null>('apiToolDraftParamPending', null)

const bindings = ref<Record<string, BoundSecret>>({})
const pending = injectedDraft ? injectedDraft.bindings : ref<Record<string, BoundSecret>>({})
const pickingName = ref<string | null>(null)
const pickerSecretId = ref<string | null>(null)
const pickerItem = ref<CredentialPickerItem | null>(null)
/** 单值页里已配凭据的参数（参数名 → 凭据标识），多值页据此提示「会被覆盖」 */
const singleValueParams = computed(() => {
  const map: Record<string, string> = {}
  for (const [name, binding] of Object.entries(bindings.value)) {
    if (binding?.secret_key) map[name] = binding.secret_key
  }
  for (const [name, rule] of Object.entries(ruleDrafts.value)) {
    if (rule) map[name] = map[name] || '按规则匹配'
  }
  return map
})

/** 两种配法分开摆：单值凭据一个参数一条，多值凭据一条供一组 */
const paramTab = ref<'single' | 'multi'>('single')
/** 已经为哪个 consumer 定过默认页；同一个工具不重复决定（否则手动切的 tab 会被顶回来） */
const tabDecidedFor = ref<string | null>(null)

/** 多值面板的脏状态由它自己算（字段映射 + 来源/规则改动） */
const multiPanelRef = ref<{ isDirty?: boolean } | null>(null)

/**
 * 切换「单值凭据 / 多值凭据」。
 *
 * 多值面板是 ``v-if`` 挂载的，切走即销毁，没保存的字段映射会**静默消失**；
 * 而单值页的绑定是即时落库的，切走再切回还在。两边行为反着来，用户切一次就
 * 可能丢掉刚配好的一半映射，所以有未保存改动先问一句。
 */
async function switchParamTab(tab: 'single' | 'multi') {
  if (tab === paramTab.value) return
  if (paramTab.value === 'multi' && multiPanelRef.value?.isDirty) {
    const ok = await confirm({
      title: '切换会丢弃未保存的映射',
      message: '多值凭据页还有没保存的字段映射，切到单值页就没了。确定要切换吗？',
      confirmText: '切换',
      danger: true,
    })
    if (!ok) return
  }
  paramTab.value = tab
  // 多值页保存会整表覆盖绑定，单值页那边的已绑定状态要按库里的事实重算
  await loadBindings()
}
/** 批量映射保存后的行内提示（这个组件没有全局 toast） */
const mappingHint = ref('')
const pickerBusy = ref(false)
const pickerError = ref('')
/** 绑定选中的字段路径（结构化凭据必填） */
const pickerValuePath = ref('')
/** 当前凭据是否「必须选一个字段」（结构化且字段可读） */
const pickerNeedsField = ref(false)

// ── 规则定位（第三种来源）────────────────────────────────────────────
// 与固定凭据分开存：两者是同槽位的两条独立绑定，共用一个 key 会互相覆盖。
/** 参数名 → 已保存的规则 */
const ruleDrafts = ref<Record<string, LocatorRule | null>>({})
/**
 * 参数名 → 这条规则是不是「多值凭据统一配的」。
 *
 * 多值保存会写成「N 个参数各一条 rule 绑定」，与单值页逐条配出来的**完全同形**。
 * 没有这个信息时，单值页只能看到「这个参数也有规则」，于是把一条规则映射多个字段
 * 显示成 N 条独立规则；更糟的是每行都有「改规则」——改一行只改一个参数，其余
 * N-1 个还是旧规则，多值结构被悄悄拆散。
 *
 * 单独存而不塞进 ``ruleDrafts``：那是要原样提交给后端的规则本体，
 * 多一个字段 ``parse_rule`` 会直接拒掉。
 */
const ruleGroups = ref<Record<string, { groupId: string; size: number }>>({})
/** 参数名 → 这条**固定凭据**是不是多值页铺出来的（一条凭据供多个参数） */
const fixedGroups = ref<Record<string, { groupId: string; size: number }>>({})
/** 新建态（工具还没有 id）下暂存的规则，保存后补写 */
const pendingRules = injectedDraft ? injectedDraft.rules : ref<Record<string, LocatorRule>>({})
/** 正在编辑规则的参数名；非空时显示编辑器 */
const locatorName = ref<string | null>(null)

const bindingOf = (name: string): BoundSecret | null => {
  const key = String(name || '').trim()
  if (!key) return null
  return bindings.value[key] || pending.value[key] || null
}

/** 这个参数的规则是不是多值凭据统一配的 */
const multiManagedRuleOf = (name: string): { groupId: string; size: number } | null => {
  const group = ruleGroups.value[String(name || '').trim()]
  // 组里只有一个参数不算多值：锁成只读只会让用户在单值页改不了
  return group && group.size > 1 ? group : null
}

/** 这条**固定凭据**是不是多值页铺出来的（一条凭据供多个参数各取一个字段） */
const multiManagedFixedOf = (name: string): { groupId: string; size: number } | null => {
  const group = fixedGroups.value[String(name || '').trim()]
  return group && group.size > 1 ? group : null
}

/** 单值页里有多少参数由多值组统一管理（只用于提示，不影响保存路径） */
const multiManagedCount = computed(
  () =>
    props.modelValue.filter((p) => {
      const name = String(p.name || '').trim()
      return !!name && !!(multiManagedRuleOf(name) || multiManagedFixedOf(name))
    }).length,
)

/** 按参数名取「固定凭据」那条：规则行没有 secret_key，混用会显示空白 */
const fixedBindingOf = (name: string): BoundSecret | null => {
  const binding = bindingOf(name)
  return binding && (binding.strategy || 'fixed') !== 'rule' ? binding : null
}


async function loadBindings() {
  if (!props.consumerId) {
    bindings.value = {}
    return
  }
  try {
    const res = await axios.get('/api/portal/secrets/bindings', {
      params: { consumer_type: props.consumerType, consumer_id: props.consumerId },
    })
    const next: Record<string, BoundSecret> = {}
    const nextRules: Record<string, LocatorRule | null> = {}
    const nextGroups: Record<string, { groupId: string; size: number }> = {}
    const nextFixedGroups: Record<string, { groupId: string; size: number }> = {}
    for (const row of res.data?.data || []) {
      const slot = String(row.slot || '')
      // header:X-Key 属于请求头，不是参数位
      if (!slot || slot.startsWith('header:')) continue
      // 用 is_global 判「全局」，不能用 owner_scope：规则行没有 secret_id，
      // outerjoin 之后 owner_scope 是 null，管理员配的全局规则会被这一行整条丢掉，
      // 表现就是「重新打开工具时规则显示未配」。
      if (!row.bound_by_me && !row.is_global) continue
      const name = slot.slice(slot.indexOf(':') + 1)
      if (!name) continue
      const strategy = row.strategy || 'fixed'
      // 同槽位的固定与规则是两条记录。固定那条优先占位（老界面只认它），
      // 规则单独记到 ruleDrafts，避免互相覆盖。
      if (strategy === 'rule') {
        nextRules[name] = withRulePath(row.rule, row.value_path)
        if (row.binding_group_id) {
          nextGroups[name] = {
            groupId: row.binding_group_id,
            size: row.binding_group_size || 0,
          }
        }
        continue
      }
      next[name] = {
        secret_id: row.secret_id || '',
        secret_key: row.secret_key || '',
        secret_status: row.secret_status,
        value_path: row.value_path || '',
        // 少了这个，重开弹窗时「只对你生效」判不出来
        owner_scope: row.owner_scope,
        // 少了这两个，列表里的结构化徽标和「整段 JSON」警示会全部退化成普通文本
        value_format: row.value_format || 'text',
        value_template: row.value_template || '',
        strategy,
      }
      // 固定凭据也可能是多值页铺出来的，同样要认出来
      if (row.binding_group_id) {
        nextFixedGroups[name] = {
          groupId: row.binding_group_id,
          size: row.binding_group_size || 0,
        }
      }
    }
    bindings.value = next
    ruleDrafts.value = nextRules
    ruleGroups.value = nextGroups
    fixedGroups.value = nextFixedGroups

    // 默认落在哪一页按**配置本身**定：有多值组标识就说明这份配置是多值配法，
    // 打开时落到单值页只会显示「每个参数一条规则」，看着像配置被拆散了。
    //
    // 固定凭据型也要算进来：多值页选「固定凭据」铺 N 个参数时 N 行 fixed 共享
    // 一个组标识，只判规则型的话这类配置打开会落到单值页——和 MCP 那边
    // hasMultiValueConfig 的口径不一致。组里只有 1 个参数的不算（那不是多值）。
    if (tabDecidedFor.value !== String(props.consumerId || '')) {
      tabDecidedFor.value = String(props.consumerId || '')
      const grouped = [...Object.values(nextGroups), ...Object.values(nextFixedGroups)]
      paramTab.value = grouped.some((g) => g.size > 1) ? 'multi' : 'single'
    }
  } catch {
    // 拉不到就按「未绑」显示，别挡住参数编辑
    bindings.value = {}
  }
}

watch(
  () => [props.consumerId, props.consumerType, injectedDraft?.revision?.value],
  () => { void loadBindings() },
  { immediate: true },
)

/**
 * 多值凭据页有没有没保存的字段映射。
 *
 * 工具保存（``toolApi.update``）只写工具定义，**不会**带上面板里的映射草稿——
 * 那份草稿要用户自己点「保存映射」才落库。不拦一下的话，用户改完映射直接点
 * 「保存工具」，看到「更新成功」就以为全存了，实际运行时用的还是旧映射。
 *
 * 只看多值页：单值页选凭据是即时 POST/DELETE 落库的，不存在"待保存的映射"。
 */
const hasUnsavedMapping = (): boolean =>
  paramTab.value === 'multi' && !!multiPanelRef.value?.isDirty

/** 有多少参数的来源是凭据或规则（判「有没有东西可清」） */
const boundParamCount = computed(
  () => props.modelValue.filter((p) => p.source === 'credential' || p.source === 'rule').length
)

/**
 * 一键清空：删掉这个工具所有参数的凭据绑定与规则，来源改回「固定值」。
 *
 * 参数本身（名字、类型）是工具配置的一部分，不在清空范围内——用户要的是
 * 「别再从凭据取值了」，不是把参数删了。
 *
 * 网上走 ``bindings/batch`` 全 ``strategy='none'``：后端按槽位把固定与规则
 * 两条一起删，所以多值凭据统一配的那组也会一并清掉。
 */
async function clearParamBindings() {
  const names = props.modelValue.map((p) => String(p.name || '').trim()).filter(Boolean)
  if (!names.length) return
  const grouped = names.filter((name) => multiManagedRuleOf(name)).length
  const ok = await confirm({
    title: '清空全部凭据配置',
    message:
      `将清除这个工具全部 ${names.length} 个参数的凭据绑定与规则（参数本身保留，来源改回「固定值」）。` +
      (grouped ? `\n其中 ${grouped} 个参数由多值凭据统一管理，也会一并清除。` : ''),
    confirmText: '清空',
    danger: true,
  })
  if (!ok) return
  // 新建态（工具还没有 id）：只清本地记账，保存工具时才真正落库
  if (!props.consumerId) {
    pending.value = {}
    pendingRules.value = {}
    for (const p of props.modelValue) p.source = 'literal'
    return
  }
  try {
    await axios.post('/api/portal/secrets/bindings/batch', {
      consumer_type: props.consumerType,
      consumer_id: props.consumerId,
      is_global: props.isGlobal,
      // API 工具的槽位由后端按参数名推算，这里不给 slot
      items: names.map((name) => ({ name, strategy: 'none' as const })),
    })
    bindings.value = {}
    ruleDrafts.value = {}
    ruleGroups.value = {}
    fixedGroups.value = {}
    for (const p of props.modelValue) p.source = 'literal'
    await loadBindings()
    mappingHint.value = '已清空全部凭据配置'
  } catch (e: any) {
    pickerError.value = e?.response?.data?.detail || '清空失败'
  }
}

function openPicker(name: string) {
  const key = String(name || '').trim()
  if (!key) {
    pickerError.value = '先给这个参数起个名字，再选凭据'
    return
  }
  pickingName.value = key
  const existing = bindingOf(key)
  pickerSecretId.value = existing?.secret_id || null
  pickerValuePath.value = existing?.value_path || ''
  // 回填已有的绑定：把值形态一起带上，弹窗里的字段标签才和列表上一致
  pickerItem.value = existing
    ? {
        id: existing.secret_id,
        secret_key: existing.secret_key,
        owner_scope: existing.owner_scope,
        value_format: existing.value_format,
        value_template: existing.value_template,
      } as CredentialPickerItem
    : null
  pickerError.value = ''
}

function onPicked(item: any) {
  pickerItem.value = item || null
  // 换凭据就清掉上一个凭据的字段路径：字段名在另一条凭据里通常不存在
  if (item?.id !== pickerSecretId.value) pickerValuePath.value = ''
}

/** 字段清单加载完：结构化且字段可读时，必须选一个字段才能保存。 */
function onFieldsLoaded(info: { format: string; fields: { name: string }[] }) {
  pickerNeedsField.value = info.format === 'json' && info.fields.length > 0
}

/**
 * 「你自己这条凭据，绑上来只对你生效」。
 *
 * 平台级工具的参数绑定默认是全局的，用户（包括管理员）很容易以为「配一次、
 * 所有人都用上了」。但个人凭据没法给所有人用——后端会把它降级成本人绑定，
 * 别人调用时拿到的仍是「未绑定」。这个落差必须在选中那一刻讲清楚，
 * 而不是等别人报错时才发现。
 */
const personalScopeNotice = computed(() => {
  // 个人级工具上绑自己的凭据是正常的，不该跳这个提示
  if (!props.isGlobal) return ''
  const item = pickerSecretId.value ? pickerItem.value : null
  if (!item || item.owner_scope !== 'user') return ''
  return (
    '这是你自己的凭据，绑定只对你生效：别人调用这个工具时，这个参数拿不到值' +
    '（会显示「未绑定」）。要让每个人用自己的账号，请改用「按规则匹配」。'
  )
})

function closePicker() {
  pickingName.value = null
  pickerSecretId.value = null
  pickerValuePath.value = ''
  pickerItem.value = null
  pickerError.value = ''
}

async function confirmPick() {
  const name = pickingName.value
  if (!name) return
  const picked = pickerSecretId.value ? pickerItem.value : null
  // 参数是整值注入：结构化凭据不选字段，注进去的就是一整段 JSON
  if (picked && pickerNeedsField.value && !pickerValuePath.value) {
    pickerError.value = '这条凭据是结构化的，请先选择要取用的字段'
    return
  }
  const valuePath = picked ? pickerValuePath.value : ''
  pickerBusy.value = true
  pickerError.value = ''
  try {
    // 还在新建态：工具没有 id，先记账，保存拿到 id 后由父组件调 flushPending
    if (!props.consumerId) {
      if (picked) {
        pending.value[name] = {
          secret_id: picked.id,
          secret_key: picked.secret_key,
          value_path: valuePath,
          // 新建态也要带：重开弹窗读的是 pending，缺了「只对你生效」就不显示
          owner_scope: picked.owner_scope,
          value_format: picked.value_format || 'text',
          value_template: picked.value_template || '',
        }
      } else {
        delete pending.value[name]
      }
      closePicker()
      return
    }

    if (!picked) {
      await axios.delete('/api/portal/secrets/bindings', {
        data: {
          consumer_type: props.consumerType,
          consumer_id: props.consumerId,
          name,
          is_global: props.isGlobal,
        },
      })
      // 两个 map 都要清：解绑后只要任一还留着 key，界面就还显示「已绑定」
      delete bindings.value[name]
      delete pending.value[name]
      injectedDraft?.bumpRevision?.()
    } else {
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: picked.id,
        consumer_type: props.consumerType,
        consumer_id: props.consumerId,
        // 只给参数名：槽位（path/query/body）由后端按工具的 URL 和方法算
        name,
        // 作用域由消费端声明（见 isGlobal 的说明），组件不猜。
        // 平台级工具配的绑定给全平台用；不设全局的话只有配的人自己能用。
        is_global: props.isGlobal,
        // 参数是整值注入，取哪个字段只能靠这两样表达
        value_path: valuePath,
      })
      bindings.value[name] = {
        secret_id: picked.id,
        secret_key: picked.secret_key,
        value_path: valuePath,
        owner_scope: picked.owner_scope,
        // 少了这两项，绑定标签会因为 format 为空直接隐藏「取 xxx」——
        // 用户刚选完字段却看不到自己选了什么，得刷新整页才恢复
        value_format: picked.value_format || 'text',
        value_template: picked.value_template || '',
      }
      // 已经落库了，待写记录没有意义；留着会在保存时又写一遍
      delete pending.value[name]
      injectedDraft?.bumpRevision?.()
    }
    closePicker()
  } catch (e: any) {
    pickerError.value = e?.response?.data?.detail || '保存绑定失败'
  } finally {
    pickerBusy.value = false
  }
}

/**
 * 保存工具后用真实 id 补写暂存的绑定，返回失败的参数名。
 *
 * 工具已经建好了，绑定失败不该让它回滚——但必须说出来，否则界面显示「已绑定」，
 * 实际调用拿不到值。
 */
async function flushPending(consumerId: string) {
  const entries = Object.entries(pending.value)
  if (!entries.length) return [] as string[]
  const failed: string[] = []
  const keep: Record<string, BoundSecret> = {}
  for (const [name, binding] of entries) {
    try {
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: binding.secret_id,
        consumer_type: props.consumerType,
        consumer_id: consumerId,
        name,
        is_global: props.isGlobal,
        value_path: binding.value_path || '',
      })
    } catch {
      failed.push(name)
      keep[name] = binding
    }
  }
  pending.value = keep
  // 规则同样补写。失败不阻断工具保存，但要报给调用方
  const ruleFailed: string[] = []
  for (const [name, rule] of Object.entries(pendingRules.value)) {
    const { value_path: rulePath = '', ...ruleBody } = rule
    try {
      await axios.post('/api/portal/secrets/bindings', {
        consumer_type: props.consumerType,
        consumer_id: consumerId,
        name,
        strategy: 'rule',
        rule: ruleBody,
        value_path: rulePath,
        is_global: props.isGlobal,
      })
    } catch {
      ruleFailed.push(name)
    }
  }
  pendingRules.value = {}
  return [...failed, ...ruleFailed]
}

/**
 * 批量映射保存完：把这些参数的来源也改过来。
 *
 * 绑定只说明「取哪条凭据的哪个字段」，运行时认的是参数自己的 ``source``
 * （``generic_api`` 只注入 ``source === 'credential'`` 的行）。不同步的话，
 * 库里绑好了、界面上也看不到绑定标签，调用时参数照旧是固定值或空。
 */
function onMappingSaved(payload: {
  bound: string[]
  freed: string[]
  /** 这次存的是固定凭据还是匹配规则——决定参数该标成 credential 还是 rule */
  source?: 'fixed' | 'rule'
}) {
  const bound = new Set(payload.bound)
  const freed = new Set(payload.freed)
  // 按规则保存时必须标 `rule`。原来一律标 `credential`，而规则参数没有固定绑定，
  // 下面 unboundNames 用 fixedBindingOf 一查就是空 → 红字「未选，保存会被拦住」→
  // 底部「保存」按钮直接禁用，用户配完规则反而存不了工具。
  const boundSource: SystemParam['source'] =
    payload.source === 'rule' ? 'rule' : 'credential'
  let changed = false
  const next = props.modelValue.map((p) => {
    const name = (p.name || '').trim()
    if (bound.has(name) && p.source !== boundSource) {
      changed = true
      return { ...p, source: boundSource }
    }
    // 两种来源都要能退回 literal：只判 credential 的话，原本按规则匹配的参数
    // 改回「模型填写」后 source 还停在 rule，ruleDrafts 又被 loadBindings 清空，
    // 同样会被判成未绑定而锁死保存。
    if (freed.has(name) && (p.source === 'credential' || p.source === 'rule')) {
      changed = true
      // 系统级参数不会交给模型，清空后回到固定值（literal），由用户自己补
      return { ...p, source: 'literal' as SystemParam['source'] }
    }
    return p
  })
  if (changed) emit('update:modelValue', next)
  void loadBindings()
  injectedDraft?.bumpRevision?.()
  if (changed) {
    // 不自动消失。这里的「同步」只改了本地草稿：运行时认的是参数自己的 source
    // （generic_api 只注入 source==='credential' 的行），没点「保存」写回工具，
    // 库里就只有绑定、没有来源标记，调用时根本不会用凭据——而且不报错。
    // 原来提示 4 秒后自己消失，用户只会看到绑定标签出现了，以为已经生效。
    mappingHint.value = '绑定已保存，但还要点下面的「保存」把参数来源写回工具，运行时才会用凭据'
  }
}

function openLocator(name: string) {
  const key = String(name || '').trim()
  if (!key) {
    pickerError.value = '先给这个参数起个名字，再配规则'
    return
  }
  // 清掉上一次的报错：否则打开就顶着一条红字，看着像这次又失败了
  pickerError.value = ''
  locatorName.value = key
}

/** 该参数可当键的入参名：模型可见的普通参数，去掉自己 */
function locatorArgOptions(self: string): string[] {
  return props.plainParams
    .map((name) => String(name || '').trim())
    .filter((name) => name && name !== self)
}

async function applyRule(rule: LocatorRule) {
  const name = locatorName.value
  // 先别关弹窗：原来在这里就置 null，保存失败（400/500）时弹窗直接消失，
  // 用户填的一整条规则没了，而 pickerError 只在「选凭据」弹窗里渲染，
  // 配规则时根本看不到——表现就是点了保存什么都没发生。
  if (!name) {
    locatorName.value = null
    return
  }
  // 还没有 id：先记账，保存工具后由 flushPending 补写
  if (!props.consumerId) {
    pendingRules.value[name] = rule
    ruleDrafts.value[name] = rule
    locatorName.value = null
    return
  }
  pickerBusy.value = true
  pickerError.value = ''
  const { value_path: rulePath = '', ...ruleBody } = rule
  try {
    await axios.post('/api/portal/secrets/bindings', {
      consumer_type: props.consumerType,
      consumer_id: props.consumerId,
      // 只给参数名：槽位（path/query/body）由后端按 URL 与方法推算
      name,
      strategy: 'rule',
      // value_path 属于绑定记录，不属于规则 JSON——混进 rule 里后端会判未知字段
      rule: ruleBody,
      value_path: rulePath,
      is_global: props.isGlobal,
    })
    ruleDrafts.value[name] = rule
    // 单条保存的规则**不带**组标识，后端会把这个槽位的 binding_group_id 写回 NULL——
    // 也就是说它从「多值组的一员」变成了单值配法。前端不同步清掉的话，
    // 界面还会显示「由多值凭据统一管理」，可实际上组已经散了。
    delete ruleGroups.value[name]
    injectedDraft?.bumpRevision?.()
    // 存成功了才关：开头不再无条件置 null，失败时留着弹窗让用户改
    locatorName.value = null
  } catch (e: any) {
    pickerError.value = e?.response?.data?.detail || '保存规则失败'
  } finally {
    pickerBusy.value = false
  }
}

const unboundNames = computed(() => props.modelValue
  .filter((p) => {
    const name = String(p.name || '').trim()
    if (!name) return false
    if (p.source === 'credential') return !fixedBindingOf(name)
    // 规则里没有凭据，检查的是「有没有规则」：空的规则跑起来必然未命中
    if (p.source === 'rule') return !ruleDrafts.value[name] && !pendingRules.value[name]
    return false
  })
  .map((p) => String(p.name).trim()))

watch(unboundNames, (names) => emit('unbound-changed', names), { immediate: true })

defineExpose({
  hasUnsavedMapping,
  flushPending,
  hasPending: () => Object.keys(pending.value).length > 0
    || Object.keys(pendingRules.value).length > 0,
})

// ── 固定值的变量菜单 ────────────────────────────────────────────────
const update = (index: number, patch: Partial<SystemParam>) => {
  emit('update:modelValue', props.modelValue.map((param, i) => (
    i === index ? { ...param, ...patch } : param
  )))
}

const addParam = () => {
  emit('update:modelValue', [
    ...props.modelValue,
    { name: '', type: 'string', value: '', description: '', source: 'literal' },
  ])
}

const removeParam = (index: number) => {
  openMenuIndex.value = null
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

const selectVariable = (index: number, value: string) => {
  update(index, { value })
  openMenuIndex.value = null
}

const toggleMenu = (index: number) => {
  openMenuIndex.value = openMenuIndex.value === index ? null : index
}

const onDocumentClick = () => {
  openMenuIndex.value = null
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <div>
        <p class="text-xs font-semibold text-indigo-900">系统级参数（模型不可见）</p>
        <p class="mt-1 text-[11px] leading-relaxed text-indigo-700">
          这些参数不会出现在<strong>模型可见</strong>的工具定义里，调用时由平台填值。
          「来自凭据」的参数按当前用户从凭据中心取值；固定值可以插
          <code class="font-mono">{user.user_name}</code> 这类变量。取不到时本次调用会明确失败。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span
          v-if="mappingHint"
          class="flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700"
        >
          {{ mappingHint }}
          <button
            type="button"
            class="rounded px-0.5 text-amber-500 hover:bg-amber-100 hover:text-amber-800"
            aria-label="关闭提示"
            @click="mappingHint = ''"
          >×</button>
        </span>
        <button
          v-if="paramTab === 'single'"
          type="button"
          class="rounded-md border border-indigo-300 bg-white px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
          @click="addParam"
        >+ 添加系统级参数</button>
        <!-- 一条凭据供多个参数时，单值页这几行是只读的：说清为什么改不了 -->
        <span
          v-if="paramTab === 'single' && multiManagedCount"
          class="inline-flex items-center rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700"
          :title="`${multiManagedCount} 个参数由多值凭据统一管理（一条凭据供多个参数各取一个字段）。要改其中某个参数，请到「多值凭据」页整组修改；在这一页单独改会把组拆散。`"
        >多值管理 {{ multiManagedCount }}</span>
        <!-- 配错时逐个改回「固定值」要 N 次操作，还容易漏掉某个参数 -->
        <button
          v-if="paramTab === 'single' && boundParamCount"
          type="button"
          class="rounded-md border border-rose-200 bg-white px-2.5 py-1 text-xs font-medium text-rose-600 hover:bg-rose-50"
          title="清除这个工具所有参数的凭据绑定与规则（参数本身保留）"
          @click="clearParamBindings"
        >清空全部</button>
      </div>
    </div>

    <!-- 两种配法：单值凭据逐参数配，多值凭据一条供一组 -->
    <div class="mt-2.5 flex gap-1 border-b border-indigo-100">
      <button
        v-for="tab in (['single', 'multi'] as const)"
        :key="tab"
        type="button"
        class="-mb-px border-b-2 px-3 py-1.5 text-xs transition-colors"
        :class="paramTab === tab
          ? 'border-indigo-600 font-semibold text-indigo-700'
          : 'border-transparent text-indigo-500 hover:text-indigo-700'"
        @click="switchParamTab(tab)"
      >{{ tab === 'single' ? '单值凭据' : '多值凭据' }}</button>
    </div>

    <!-- ══ 单值凭据：一个参数配一条 ══ -->
    <template v-if="paramTab === 'single'">
    <p v-if="modelValue.length === 0" class="mt-2 text-[11px] italic text-indigo-400">
      暂无系统级参数。申请人、操作人等不能由模型决定的值可以加在这里。
    </p>

    <div
      v-for="(p, index) in modelValue"
      :key="index"
      class="mt-2 flex flex-wrap items-center gap-2 rounded-md bg-white p-2 shadow-sm"
    >
      <input
        v-model="p.name"
        class="w-40 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        placeholder="参数名"
      />
      <select
        v-model="p.type"
        class="w-24 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
      >
        <option v-for="option in SYSTEM_PARAM_TYPES" :key="option" :value="option">{{ option }}</option>
      </select>
      <select
        v-model="p.source"
        class="w-28 rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        :title="'值的来源：固定值，或按当前用户从凭据中心解析'"
      >
        <option v-for="option in SYSTEM_PARAM_SOURCES" :key="option" :value="option">
          {{ option === 'credential' ? '固定凭据' : option === 'rule' ? '按规则匹配' : '固定值' }}
        </option>
      </select>

      <!-- 来自凭据：就地选，不再跳去「高级请求配置」 -->
      <div v-if="p.source === 'credential'" class="flex flex-wrap items-center gap-1.5">
        <!-- 多值页铺出来的固定凭据不给「换」：换了只影响这一个参数，
             组里其余参数还指向原凭据，组标识还在但语义已经散了 -->
        <span
          v-if="multiManagedFixedOf(p.name)"
          class="inline-flex shrink-0 items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700"
          title="这条凭据由「多值凭据」一次性选好、供组内多个参数各取一个字段。要换请到多值凭据页整组换，在这里单独换会把它拆散。"
        >由多值凭据统一管理（{{ multiManagedFixedOf(p.name)?.size }} 个参数）</span>
        <button
          v-else
          type="button"
          class="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium shadow-sm transition-colors"
          :class="fixedBindingOf(p.name)
            ? 'border border-violet-300 bg-white text-violet-700 hover:bg-violet-50'
            : 'bg-violet-600 text-white hover:bg-violet-700'"
          @click.stop="openPicker(p.name)"
        >
          <KeyIcon class="h-3 w-3" />
          {{ fixedBindingOf(p.name) ? '换凭据' : '选凭据' }}
        </button>
        <!-- 只给标识不够：结构化凭据还要说清取的是哪个字段 -->
        <span
          v-if="fixedBindingOf(p.name)"
          class="max-w-[16rem] font-mono text-[11px]"
          :class="bindingIssue(fixedBindingOf(p.name)) ? 'text-amber-700' : 'text-emerald-700'"
          :title="bindingIssue(fixedBindingOf(p.name))
            ? `已绑定 ${fixedBindingOf(p.name)?.secret_key}，但${bindingIssue(fixedBindingOf(p.name))}`
            : ''"
        >
          <SecretBindingLabel
            :secret-key="fixedBindingOf(p.name)?.secret_key"
            :format="fixedBindingOf(p.name)?.value_format"
            :template="fixedBindingOf(p.name)?.value_template"
            :value-path="fixedBindingOf(p.name)?.value_path"
          />
        </span>
        <!-- 「未选」只在真的没绑时出现。此前的 v-else 挂在内层 `bindingIssue`
             判断上，绑好且没问题时 issue 为空 → 也落到这里，明明绑好了却显示红字。 -->
        <template v-if="fixedBindingOf(p.name)">
          <span
            v-if="bindingIssue(fixedBindingOf(p.name))"
            class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
          >{{ bindingIssue(fixedBindingOf(p.name)) }}</span>
        </template>
        <span v-else class="text-[11px] text-red-600">未选，保存会被拦住</span>
        <span
          v-if="!consumerId && pending[p.name.trim()]"
          class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
        >保存后生效</span>
      </div>

            <!-- 按规则匹配：不选具体凭据，只配「怎么找」 -->
      <div v-else-if="p.source === 'rule'" class="flex flex-wrap items-center gap-1.5">
        <!-- 多值凭据统一配的规则不给「改规则」：改了只影响这一个参数，
             组里其余参数还是旧规则，多值结构被悄悄拆散，而这一页看不出来 -->
        <template v-if="multiManagedRuleOf(p.name)">
          <span
            class="inline-flex shrink-0 items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700"
            title="这条规则由「多值凭据」一次性配置，组内所有参数共用同一条规则、各取不同字段。要改请到多值凭据页整组修改，在这里单独改会把它拆散。"
          >由多值凭据统一管理{{ multiManagedRuleOf(p.name)?.size ? `（${multiManagedRuleOf(p.name)?.size} 个参数）` : '' }}</span>
          <span
            v-if="ruleDrafts[p.name.trim()]"
            class="max-w-[16rem] truncate text-[11px] text-indigo-600"
            :title="ruleDrafts[p.name.trim()]?.key_source"
          >{{ ruleSummary(ruleDrafts[p.name.trim()]) }}</span>
        </template>
        <template v-else>
          <button
            type="button"
            class="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium shadow-sm transition-colors"
            :class="ruleDrafts[p.name.trim()]
              ? 'border border-emerald-300 bg-white text-emerald-700 hover:bg-emerald-50'
              : 'bg-emerald-600 text-white hover:bg-emerald-700'"
            @click.stop="openLocator(p.name)"
          >
            <KeyIcon class="h-3 w-3" />
            {{ ruleDrafts[p.name.trim()] ? '改规则' : '配规则' }}
          </button>
          <span
            v-if="ruleDrafts[p.name.trim()]"
            class="max-w-[16rem] truncate text-[11px] text-emerald-700"
            :title="ruleDrafts[p.name.trim()]?.key_source"
          >{{ ruleSummary(ruleDrafts[p.name.trim()]) }}</span>
          <span v-else class="text-[11px] text-red-600">未配，保存会被拦住</span>
        </template>
        <span
          v-if="!consumerId && pendingRules[p.name.trim()]"
          class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
        >保存后生效</span>
      </div>

<div v-else class="relative flex items-center gap-1">
        <input
          v-model="p.value"
          class="w-40 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          placeholder="{user.user_name}"
        />
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-md border border-gray-300 bg-white font-mono text-[11px] font-bold text-gray-600 hover:bg-gray-50"
          :title="`为系统级参数 ${p.name || index + 1} 插入用户变量`"
          :aria-label="`为系统级参数 ${p.name || index + 1} 插入用户变量`"
          :aria-expanded="openMenuIndex === index"
          @click.stop="toggleMenu(index)"
        >{{ '{}' }}</button>
        <PermissionVariableMenu
          v-if="openMenuIndex === index"
          :builtin-variables="builtinVariables"
          :extra-data-variables="extraDataVariables"
          accent="slate"
          @click.stop
          @select="selectVariable(index, $event)"
        />
      </div>

      <input
        v-model="p.description"
        class="min-w-[10rem] flex-1 rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        placeholder="描述（仅编辑和试跑提示）"
      />
      <button
        type="button"
        class="rounded-md p-1 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
        :title="`删除系统级参数 ${p.name || index + 1}`"
        :aria-label="`删除系统级参数 ${p.name || index + 1}`"
        @click="removeParam(index)"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>    </template>

    <!-- ══ 多值凭据：一条凭据供一组参数 ══ -->
    <div v-else class="mt-2">
      <p v-if="!consumerId" class="rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
        工具还没保存。先保存工具，再回来把这条凭据的字段分给各个参数。
      </p>
      <SecretFieldMappingPanel
        v-else
        ref="multiPanelRef"
        :consumer-type="consumerType"
        :consumer-id="consumerId"
        :is-global="isGlobal"
        :params="modelValue.map((p) => p.name).filter(Boolean)"
        :single-value-params="singleValueParams"
        :subject="'本次工具的参数'"
        @saved="onMappingSaved"
      />
    </div>

  </div>

  <Teleport to="body">
    <div
      v-if="pickingName"
      class="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4"
      @click.self="closePicker"
    >
      <div class="w-full max-w-md rounded-xl bg-white p-4 shadow-xl">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h3 class="text-sm font-semibold text-gray-900">为参数绑定凭据</h3>
            <p class="mt-0.5 font-mono text-xs text-gray-500">{{ pickingName }}</p>
          </div>
          <button type="button" class="text-gray-400 hover:text-gray-600" @click="closePicker">✕</button>
        </div>

        <p class="mt-2 text-[11px] leading-relaxed text-gray-500">
          调用时按<strong>当前用户</strong>解析；换用户就换值，所以不会写进工具配置。
        </p>

        <div class="mt-3 space-y-2">
          <CredentialPicker v-model="pickerSecretId" scene="api_tool" @picked="onPicked" />
          <!-- 参数是整值注入：结构化凭据不选字段，注进去的就是一段 JSON -->
          <SecretFieldSelect
            v-if="pickerSecretId"
            v-model="pickerValuePath"
            :secret-id="pickerSecretId"
            @loaded="onFieldsLoaded"
          />
        </div>

        <p
          v-if="personalScopeNotice && !pickerError"
          class="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-700"
        >{{ personalScopeNotice }}</p>
        <p v-if="pickerError" class="mt-2 text-[11px] text-red-600">{{ pickerError }}</p>
        <p v-else-if="!personalScopeNotice && bindingOf(pickingName)" class="mt-2 text-[11px] text-emerald-600">
          当前已绑定 {{ bindingOf(pickingName)?.secret_key }}
        </p>

        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
            @click="closePicker"
          >取消</button>
          <button
            type="button"
            :disabled="pickerBusy"
            class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
            @click="confirmPick"
          >{{ pickerBusy ? '保存中…' : '确定' }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 规则编辑器必须和「绑定凭据」弹窗平级：嵌进 v-if="pickingName" 里面的话，
       点「配规则」只设 locatorName、不动 pickingName，整个子树不渲染 → 点了没反应 -->
  <!-- show-value-path=false：同上，参数入口整段注入，不需要选字段 -->
  <!-- 绑定/规则保存失败要能看见：pickerError 原来只在「选凭据」弹窗里渲染，
     配规则时那个弹窗是关着的，报错等于被吞掉。 -->
<Teleport to="body">
  <div
    v-if="pickerError && !pickingName"
    class="fixed inset-x-0 top-4 z-[130] mx-auto w-fit max-w-2xl rounded-md border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-700 shadow-lg"
    role="alert"
  >
    {{ pickerError }}
    <button class="ml-3 font-semibold text-rose-500 hover:text-rose-700" @click="pickerError = ''">
      知道了
    </button>
  </div>
</Teleport>

<SecretLocatorEditor
    :open="Boolean(locatorName)"
    :rule="locatorName ? (ruleDrafts[locatorName] || null) : null"
    :slot="locatorName || ''"
    :consumer-type="consumerType"
    :arg-options="locatorName ? locatorArgOptions(locatorName) : []"
    :title="locatorName ? `凭据匹配规则 · ${locatorName}` : '凭据匹配规则'"
      :show-value-path="false"
      :show-value-template="false"
    @update:open="(value: boolean) => { if (!value) locatorName = null }"
    @save="applyRule"
  />

</template>
