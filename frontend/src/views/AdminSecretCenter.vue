<script setup lang="ts">
/**
 * 凭据中心（系统管理 → 凭据中心）。
 *
 * 与个人中心的「我的凭据」共用同一套表和同一个注入层，差别只在管理面：
 * 管理员能看到全部系统级凭据、能授权/撤销、能停用/恢复、能删除。
 *
 * **管理员的权限边界**（不是前端限制，是后端就不给）：
 * - 看不到任何明文——`credential` 建完即不可读，管理员也没有解密接口；
 * - 不能停用个人凭据（那是用户自己的资产）。
 *
 * 停用与删除是**两档不同强度**的动作，别混用：停用可逆（`/admin/{id}/status`
 * 随时能恢复），删除不可逆（`DELETE /secrets/{id}` 会连授权和绑定一起清掉，
 * 只保留使用流水与变更记录）。疑似泄漏先停用，确认为废数据才删除。
 */
import { computed, onMounted, ref } from 'vue'
import SecretUsageStats from '../components/secrets/SecretUsageStats.vue'
import CredentialValueEditor from '../components/secrets/CredentialValueEditor.vue'
import SecretFormatBadge from '../components/secrets/SecretFormatBadge.vue'
import { credentialTemplateLabel } from '../utils/credentialTemplates'
import SecretRowActions from '../components/secrets/SecretRowActions.vue'
import SecretKeyChip from '../components/secrets/SecretKeyChip.vue'
import { resultLabel } from '../utils/secretLabels'
// 变更记录的「动作」文案与配色是共享模块提供的：模板里直接用 secretActionLabel /
// secretActionStyle，漏了这行 import 不会在构建期报错，只会在打开弹框、真的要渲染
// 列表时抛 undefined is not a function——表现就是「点了没反应 / 弹框空白」。
import { SECRET_ACTION_LABEL, secretActionLabel, secretActionStyle } from '../utils/secretAudit'
import UserDeptPicker from '../components/common/UserDeptPicker.vue'
import { useDismissible } from '../utils/dismissible'
import axios from '../utils/axios'
import { shortTime } from '../utils/date'
import {
  formatLocalDateTimeSummary,
  isoToLocalDateTimeInput,
  localDateTimeInputToIso,
} from '../utils/secretExpiry'

interface Grant {
  id: string
  grantee_type: 'user' | 'dept' | 'all'
  grantee_id: string
  /** 后端解析好的显示名；解析不到（对象已被删）时为空，界面回退显示 id */
  grantee_name?: string | null
  expires_at: string | null
}

interface SecretBindingRef {
  id: string
  slot: string
  consumer_type: string
  consumer_id: string
  /** 后端解析好的目标名；目标已删除时退回 id */
  consumer_name?: string
  bound_by_user_id: string | null
  /** 绑定者的显示名；全局绑定为空 */
  bound_by_name?: string | null
  is_global?: boolean
  /** MCP 参数槽位里的工具短名 */
  tool_name?: string | null
  /**
   * 同槽位存在 rule 绑定时，这条 fixed 绑定被压制的程度（仅 fixed 有值）：
   * `fail` 永远轮不到 / `fallback_fixed` 规则未命中才回退 / `model` 需模型指定。
   */
  rule_shadow?: 'fail' | 'fallback_fixed' | 'model' | null
}

interface SystemSecret {
  id: string
  secret_key: string
  name: string
  secret_type: 'credential' | 'variable'
  status: string
  allowed_scenarios: string[]
  allowed_hosts: string[]
  category: string | null
  remark: string | null
  value_masked: string
  // 值形态：text 单值 / json 结构化。老记录没有这两个字段，按单值处理
  value_format?: 'text' | 'json'
  value_template?: string | null
  /** 非敏感字段拼成的摘要（host · port · 用户名），凭证类也有 */
  value_summary?: string | null
  grants: Grant[]
  grant_count: number
  expires_at: string | null
  last_rotated_at: string | null
  // 后端 serialize_secret 一直在回这两个字段，只是卡片之前没显示
  created_at?: string | null
  updated_at?: string | null
  // 后端 admin_list 一次带出来的聚合：绑到哪、用过没有
  bindings: SecretBindingRef[]
  binding_count: number
  usage_7d: number
  failed_7d: number
  last_used_at: string | null
}

const SCENE_LABEL: Record<string, string> = {
  mcp: 'MCP',
  api_tool: 'API 工具',
  script_env: '脚本',
  // 历史数据里可能还有这个取值，标签留着才能正常显示老记录
  scheduled_task: '定时任务（历史）',
  agent: '智能体',
  // 只在「使用记录」里出现的场景：读变量类凭据的明文
  variable_read: '读取变量',
}

// 已废弃的场景：后端常量还留着（历史数据里有这个取值），但代码里早就不参与
// 任何判定。继续显示会让人以为「勾了就生效」，展示层统一过滤掉。
/**
 * 槽位转成人话。
 *
 * `arg:echo:api_key` 对使用者没有意义，他要看的是「这个服务的哪个参数」；
 * `header:X-Key` 同理——去掉前缀只留名字就够了。
 */
function slotLabel(binding: SecretBindingRef): string {
  const slot = String(binding.slot || '')
  if (slot.startsWith('arg:')) {
    const parts = slot.split(':')
    // arg:<短工具名>:<参数名>
    return parts.length >= 3 ? `${parts[1]}.${parts.slice(2).join(':')}` : slot.slice(4)
  }
  const idx = slot.indexOf(':')
  return idx === -1 ? slot : slot.slice(idx + 1)
}

/** 绑定槽位挂在谁身上——光看 slot 不知道是 MCP 服务还是脚本 */
const BINDING_CONSUMER_LABEL: Record<string, string> = {
  mcp_server: 'MCP 服务',
  api_tool: 'API 工具',
  script_tool: '脚本工具',
  // 历史数据里出现过这两个取值
  script: '脚本',
  script_env: '脚本',
}

/** 分组顺序固定：MCP → API 工具 → 脚本 → 其它，不随绑定创建时间跳动。 */
const BINDING_TYPE_ORDER = ['mcp_server', 'api_tool', 'script_tool', 'script', 'script_env']

/** 每类一个配色，扫一眼就知道「这条凭据被哪些地方用」 */
const BINDING_TYPE_STYLE: Record<string, string> = {
  mcp_server: 'bg-violet-50 text-violet-700',
  api_tool: 'bg-sky-50 text-sky-700',
  script_tool: 'bg-amber-50 text-amber-700',
  script: 'bg-amber-50 text-amber-700',
  script_env: 'bg-amber-50 text-amber-700',
}

interface BindingGroup {
  type: string
  label: string
  style: string
  items: SecretBindingRef[]
}

/** 把绑定按消费者类型分组；混在一起看不出「被哪几类地方用」。 */
/**
 * 同一槽位上还有规则绑定时，这条固定绑定还能不能被选中。
 *
 * `on_miss=fail` 的规则一旦未命中就整条失败，**不会**回退到固定绑定——
 * 这种固定绑定是死数据，界面上必须能看出来，否则「已绑定 N 处」等于骗人。
 */
function bindingShadowHint(binding: SecretBindingRef): string {
  const shadow = binding.rule_shadow
  if (shadow === 'fail') {
    return '\n⚠ 同槽位有规则绑定且 on_miss=fail：规则未命中时会整条失败，不回退到这条固定绑定，所以它目前不会生效'
  }
  if (shadow === 'fallback_fixed') return '\n同槽位有规则绑定：只有规则未命中时才会回退到这条'
  if (shadow === 'model') return '\n同槽位有规则绑定：只有模型指定了这条凭据才会用到'
  return ''
}

function groupBindings(bindings: SecretBindingRef[]): BindingGroup[] {
  const buckets = new Map<string, SecretBindingRef[]>()
  for (const binding of bindings) {
    const key = String(binding.consumer_type || 'other')
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key)!.push(binding)
  }
  const rank = (type: string) => {
    const idx = BINDING_TYPE_ORDER.indexOf(type)
    return idx === -1 ? BINDING_TYPE_ORDER.length : idx
  }
  return [...buckets.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]))
    .map(([type, items]) => ({
      type,
      label: BINDING_CONSUMER_LABEL[type] || type,
      style: BINDING_TYPE_STYLE[type] || 'bg-gray-100 text-gray-600',
      items,
    }))
}

/**
 * 绑定 chip 默认每组只露前几条。
 *
 * 一条结构化凭据常被同一台 MCP 服务的多个参数位各绑一次（host / port /
 * username / password …），全量平铺会把卡片撑得很高，同屏能看的凭据条数
 * 直接掉一半——扫列表时更需要的恰恰是「有几张卡」。
 */
const BINDING_PREVIEW_COUNT = 3
const expandedBindingGroups = ref<string[]>([])

const bindingGroupKey = (itemId: string, type: string) => `${itemId}:${type}`

function isBindingGroupExpanded(itemId: string, type: string): boolean {
  return expandedBindingGroups.value.includes(bindingGroupKey(itemId, type))
}

function toggleBindingGroup(itemId: string, type: string): void {
  const key = bindingGroupKey(itemId, type)
  expandedBindingGroups.value = expandedBindingGroups.value.includes(key)
    ? expandedBindingGroups.value.filter((value) => value !== key)
    : [...expandedBindingGroups.value, key]
}

function shownBindingItems(itemId: string, group: BindingGroup): SecretBindingRef[] {
  return isBindingGroupExpanded(itemId, group.type)
    ? group.items
    : group.items.slice(0, BINDING_PREVIEW_COUNT)
}

const DEPRECATED_SCENES = new Set(['scheduled_task'])
const visibleScenarios = (keys: string[]) => keys.filter((key) => !DEPRECATED_SCENES.has(key))

// 与个人中心同一套口径：真接通的才能勾，`agent` 没有注入点。
const CREATE_SCENES = [
  { key: 'mcp', label: 'MCP 服务', desc: '作为连接头的值，在建立 MCP 连接时注入', wired: true },
  { key: 'api_tool', label: 'API 工具', desc: '填进工具的请求头，或声明为「来自凭据」的参数位', wired: true },
  { key: 'script_env', label: '脚本环境变量', desc: '以环境变量形式进子进程；平台与个人两层开关都开才生效', wired: true },
  { key: 'agent', label: '智能体', desc: '暂未接入，勾了也不会注入', wired: false },
]

const sceneDescOf = (key: string) => CREATE_SCENES.find((scene) => scene.key === key)?.desc || ''

// 一次性说明，关掉后记住
const { visible: showNoGrantHint, dismiss: dismissNoGrantHint } = useDismissible('admin-secrets:no-grant')
const { visible: showPlaintextHint, dismiss: dismissPlaintextHint } = useDismissible('admin-secrets:plaintext')

const loading = ref(false)
const items = ref<SystemSecret[]>([])
const keyword = ref('')

const toast = ref<{ text: string; type: 'ok' | 'err' } | null>(null)
function showToast(text: string, type: 'ok' | 'err' = 'ok') {
  toast.value = { text, type }
  window.setTimeout(() => (toast.value = null), 2600)
}

// ── 到期与失败：卡片上最该先看到的两件事 ──────────────────────────────
/**
 * 7 天内到期才算「需要注意」。
 *
 * 后端 resolver 在 `expires_at <= now` 时会直接不注入（`injection.py` 里给出的
 * 理由是「凭据已过期」），所以到期不是提示而是**已经开始坏了**：授权还在、绑定
 * 还在、调用却全失败。这种「配置看起来完好、就是不生效」最难查，必须提前说。
 */
const EXPIRING_SOON_DAYS = 7

/** 距到期还有几天；负数表示已经过期，没有有效期返回 null。 */
function daysLeft(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return null
  return Math.ceil((ts - Date.now()) / 86_400_000)
}

/**
 * 卡片右上角的到期徽标文案（短），不需要提醒时返回空串。
 *
 * 徽标要挤在名称后面、还要和 ⋯ 并列，所以只给「几天后到期」这种最短形式；
 * 「过期会怎样」放在 `expiryHint` 里当悬停说明——那句才是真正让人动手的话。
 */
function expiryBadge(item: SystemSecret): string {
  const left = daysLeft(item.expires_at)
  if (left === null) return ''
  if (left < 0) return '已过期'
  if (left === 0) return '今天到期'
  if (left <= EXPIRING_SOON_DAYS) return `${left} 天后到期`
  return ''
}

/** 到期徽标的悬停说明：说清后果与确切时间 */
function expiryHint(item: SystemSecret): string {
  const left = daysLeft(item.expires_at)
  if (left === null) return ''
  const at = item.expires_at ? shortTime(item.expires_at) : ''
  if (left < 0) {
    return `有效期已过（${at}）：授权与绑定还在，但调用会被直接拒绝（「凭据已过期」）。轮换一次即可恢复。`
  }
  return `有效期至 ${at}。到期后授权与绑定仍然保留，但调用会被拒绝——过期前轮换一次就行。`
}

/** 已经过期（不是「快过期」）——徽标颜色分档要用 */
function isExpired(item: SystemSecret): boolean {
  const left = daysLeft(item.expires_at)
  return left !== null && left < 0
}

/** 左侧色条：生效绿 / 快过期或已过期琥珀 / 停用灰 */
function cardAccent(item: SystemSecret): string {
  if (item.status !== 'active') return 'border-l-gray-300'
  const left = daysLeft(item.expires_at)
  if (left !== null && left <= EXPIRING_SOON_DAYS) return 'border-l-amber-500'
  return 'border-l-emerald-400'
}

// ── 状态筛选 ──────────────────────────────────────────────────────────
// 顶部指标卡只说「有 4 条未授权」，真正要处理的是**哪几条**。一排 chips 把
// 「翻卡片找」变成一次点击；计数直接来自同一份数据，不会和指标卡对不上。
type StatusFilter = 'all' | 'active' | 'disabled' | 'unbound' | 'expiring' | 'failing'
const statusFilter = ref<StatusFilter>('all')

function matchesStatus(item: SystemSecret, filter: StatusFilter): boolean {
  switch (filter) {
    case 'active':
      return item.status === 'active'
    case 'disabled':
      return item.status !== 'active'
    case 'unbound':
      // 「未绑定」比「未授权」更接近故障现场：没有消费者用它，就永远不会被调用
      return item.binding_count === 0
    case 'expiring':
      return expiryBadge(item) !== ''
    case 'failing':
      return item.failed_7d > 0
    default:
      return true
  }
}

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return items.value.filter((item) => {
    if (!matchesStatus(item, statusFilter.value)) return false
    if (!q) return true
    return item.name.toLowerCase().includes(q) || item.secret_key.toLowerCase().includes(q)
  })
})

// ── 视图模式（卡片 / 列表） ────────────────────────────────────────────
// 存在 localStorage 里：这是个人偏好，不该占一次后端请求；换浏览器回到默认
// 卡片视图也无所谓（卡片是信息最全的那个）。
const VIEW_KEY = 'dsh:admin-secrets:view'
const viewMode = ref<'card' | 'list'>(
  (() => {
    try {
      return window.localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'card'
    } catch {
      return 'card'
    }
  })(),
)
function setViewMode(mode: 'card' | 'list') {
  viewMode.value = mode
  try {
    window.localStorage.setItem(VIEW_KEY, mode)
  } catch {
    // 隐私模式下存不下，本次会话内仍然生效
  }
}

/**
 * 授权对象的显示名。
 *
 * 只写「用户 · 62」等于没写：管理员拿这个数字判断不了是谁，得先去用户表反查，
 * 而这一栏问的恰恰就是「这条凭据给了谁」。所以用户显示成 `姓名（id）`，
 * 括号里的 id 留着——它是撤销授权、查审计时唯一能对上号的值，删了就再没有
 * 定位手段；重名的人也只有 id 能区分。
 *
 * 名字拿不到（对象被删、或后端没解析出来）就回退成裸 id，不编造。
 */
function grantLabel(grant: Grant): string {
  if (grant.grantee_type === 'all') return '全体用户'
  const name = String(grant.grantee_name || '').trim()
  const id = String(grant.grantee_id || '')
  if (grant.grantee_type === 'dept') return name || `部门 · ${id}`
  if (!name) return `用户 · ${id}`
  return `${name}（${id}）`
}

/**
 * 授权 chip 的悬停提示。
 *
 * 不再重复一遍可见文字：那个信息已经在 chip 上了，悬停应当补上 chip 放不下的
 * 部分——授权的到期时间。到期后这条授权就不再匹配，是「明明授过权却取不到值」
 * 时最先要排除的一环。
 */
function grantHint(grant: Grant): string {
  const label = grantLabel(grant)
  return grant.expires_at ? `${label}\n授权到期：${shortTime(grant.expires_at)}` : label
}

//: 卡片里最多平铺几个授权对象，其余收进「+N」。
//: 一条凭据授权给几十个人是常态，全摊开会把卡片撑成一面墙。
const GRANT_PREVIEW_COUNT = 3

/** 展开态按凭据 id 记：一个页面里多张卡，不能一次全展开 */
const expandedGrantIds = ref<string[]>([])

function grantGroupExpanded(secretId: string): boolean {
  return expandedGrantIds.value.includes(secretId)
}

function toggleGrantGroup(secretId: string) {
  expandedGrantIds.value = grantGroupExpanded(secretId)
    ? expandedGrantIds.value.filter((item) => item !== secretId)
    : [...expandedGrantIds.value, secretId]
}

function visibleGrants(item: SystemSecret): Grant[] {
  if (grantGroupExpanded(item.id) || item.grants.length <= GRANT_PREVIEW_COUNT) {
    return item.grants
  }
  return item.grants.slice(0, GRANT_PREVIEW_COUNT)
}

function hiddenGrantCount(item: SystemSecret): number {
  if (grantGroupExpanded(item.id)) return 0
  return Math.max(0, item.grants.length - GRANT_PREVIEW_COUNT)
}

/** 列表列宽有限，授权范围只给摘要；完整名单挂在 title 里 */
function grantSummary(item: SystemSecret): string {
  if (!item.grants.length) return '未授权'
  if (item.grants.some((grant) => grant.grantee_type === 'all')) return '全体用户'
  // noUncheckedIndexedAccess 下 `grants[0]` 是 `Grant | undefined`，
  // 上面的 length 判断 TS 不认，得显式收一次
  const first = item.grants[0]
  if (!first) return `${item.grants.length} 个对象`
  // 一个对象时直接把名字写出来，省一次悬停；多个才退化成「首个 等 N 个」
  if (item.grants.length === 1) return grantLabel(first)
  return `${grantLabel(first)} 等 ${item.grants.length} 个`
}

function grantTitle(item: SystemSecret): string {
  if (!item.grants.length) return '尚未授权给任何人，除管理员外不可见'
  return item.grants.map((grant) => grantLabel(grant)).join('\n')
}

/** 绑定明细在列表里放不下，收进 title —— 但「绑到哪」恰恰是排错第一步 */
function bindingTitle(item: SystemSecret): string {
  if (!item.bindings.length) return '还没有绑到任何地方：建了但不会被任何调用用到'
  return groupBindings(item.bindings)
    .map(
      (group) =>
        `${group.label}：` +
        group.items
          .map((binding) => `${binding.consumer_name || binding.consumer_id} → ${slotLabel(binding)}`)
          .join('、'),
    )
    .join('\n')
}

const stats = computed(() => {
  const active = items.value.filter((i) => i.status === 'active').length
  const credentials = items.value.filter((i) => i.secret_type === 'credential').length
  const grantees = new Set(
    items.value.flatMap((i) => i.grants.map((g) => `${g.grantee_type}:${g.grantee_id}`)),
  )
  // 授权对象的构成：一个「全体用户」和一个具体用户对排查的意义完全不同
  const grantUsers = new Set<string>()
  const grantDepts = new Set<string>()
  let grantAll = 0
  for (const item of items.value) {
    for (const grant of item.grants) {
      if (grant.grantee_type === 'all') grantAll += 1
      else if (grant.grantee_type === 'dept') grantDepts.add(grant.grantee_id)
      else grantUsers.add(grant.grantee_id)
    }
  }
  const noGrant = items.value.filter((i) => i.grant_count === 0 && i.status === 'active').length
  const unbound = items.value.filter((i) => i.binding_count === 0 && i.status === 'active').length
  const expiring = items.value.filter((i) => i.status === 'active' && expiryBadge(i) !== '').length
  const failing = items.value.filter((i) => i.failed_7d > 0).length
  // 第 4 格合成「需要注意」：分开数两个数字，管理员还得自己做加法才知道要不要动手
  return {
    total: items.value.length,
    credentials,
    variables: items.value.length - credentials,
    active,
    disabled: items.value.length - active,
    grantees: grantees.size,
    grantUsers: grantUsers.size,
    grantDepts: grantDepts.size,
    grantAll,
    noGrant,
    unbound,
    expiring,
    failing,
    attention: unbound + expiring,
  }
})

/**
 * 筛选 chip 的清单。
 *
 * 计数与 `matchesStatus` 共用同一份数据，所以 chip 上的数字一定等于点进去的条数；
 * 分开算一次是「点了 3 条却出来 4 条」这类 bug 的常见来源。
 */
const STATUS_FILTERS = computed(() => {
  const list: Array<{ key: StatusFilter; label: string; count: number; warn?: boolean }> = [
    { key: 'all', label: '全部', count: items.value.length },
    { key: 'active', label: '生效中', count: stats.value.active },
    { key: 'disabled', label: '已停用', count: stats.value.disabled },
    { key: 'unbound', label: '未绑定', count: stats.value.unbound, warn: true },
    { key: 'expiring', label: '7 天内到期', count: stats.value.expiring, warn: true },
    { key: 'failing', label: '近 7 天有失败', count: stats.value.failing, warn: true },
  ]
  return list
})

async function load() {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/secrets/admin')
    items.value = res.data?.data || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载失败', 'err')
  } finally {
    loading.value = false
  }
}

// ── 新建系统级凭据 ────────────────────────────────────────────────────
const showCreate = ref(false)
const createStep = ref(1)
const createBusy = ref(false)
const form = ref({
  secret_key: '',
  name: '',
  value: '',
  secret_type: 'credential' as 'credential' | 'variable',
  allowed_scenarios: ['mcp'] as string[],
  allowed_hosts: '',
  expires_at: '',
  // 值形态与模板标识：只在「结构化」档下写库，单值档一律回落 text
  value_format: 'text' as 'text' | 'json',
  value_template: null as string | null,
  value_secret_fields: [] as string[],
})

//: 值本身是否可提交（结构化时由字段校验决定，不只是「非空」）
const valueValid = ref(false)

// ── 变更记录（管理员看全局） ──────────────────────────────────────────
// 与「使用记录」分开：一个记「谁用了」，一个记「谁改了」。管理员最常问的后一个
// 问题——「这条 token 谁轮换过」——以前一条都答不上。
const auditOpen = ref(false)
const auditLoading = ref(false)
type AuditRow = {
  id: string
  secret_key: string | null
  action: string
  actor_id: string | null
  /** 后端解析好的操作人显示名（actor_name 历史数据常为空） */
  actor_label?: string | null
  /** 后端把 detail JSON 翻译成的一句话 */
  detail_text?: string | null
  detail: string | null
  created_at: string
}
const auditRows = ref<AuditRow[]>([])
/**
 * 只看某一条凭据的变更（从卡片进来时带上）。
 *
 * 全局列表里翻「这条凭据到底经历过什么」要在一屏几十条里靠眼睛筛，而卡片上
 * 问的恰恰就是这一条——所以入口要能带 secret_id 进来，而不是让用户自己再过滤。
 */
const auditSecretId = ref('')
const auditSecretKey = ref('')
/** 关键字：凭据标识 / 操作人 / detail 里的槽位与规则，后端一次搜三处 */
const auditKeyword = ref('')
/** 动作多选，空 = 全部 */
const auditActions = ref<string[]>([])
/** 用过的筛选条件（用于区分「一条记录都没有」和「筛完没匹配」） */
const auditFiltered = computed(
  () => Boolean(auditSecretId.value || auditKeyword.value.trim() || auditActions.value.length),
)

/** 动作筛选项的候选：文案表就是唯一真源，别在这里再列一遍 */
const AUDIT_ACTIONS = Object.keys(SECRET_ACTION_LABEL)

// ── 使用记录（管理员看全局） ──────────────────────────────────────────
// 「近 7 天调用 N 次」只给了个数字，查不到是谁、在哪个场景、成没成功。
// 排查「某个用户说用不了」时，这两个问题才是关键。
const usageOpen = ref(false)
const usageLoading = ref(false)
type UsageRow = {
  id: string
  secret_key: string | null
  user_id: string | null
  user_name: string | null
  user_real_name: string | null
  user_email: string | null
  scene: string
  consumer_type: string | null
  slot: string | null
  target_host: string | null
  result: string
  /** 会话 id：排查时要点回那次对话 */
  conversation_id?: string | null
  /** 单次调用的追踪号 */
  trace_id?: string | null
  /** 聚合后：这次调用一共注入了几条参数 */
  slot_count?: number
  /** 聚合后：其中有几条没成功 */
  failed_count?: number
  /** 聚合后：各参数明细，展开才显示 */
  slots?: { slot: string | null; result: string }[]
  created_at: string
}
const usageRows = ref<UsageRow[]>([])
/** 展开了参数明细的流水行（按行 id 记） */
const expandedUsageKeys = ref<string[]>([])

function toggleUsageExpand(row: UsageRow) {
  const key = String(row.id)
  expandedUsageKeys.value = expandedUsageKeys.value.includes(key)
    ? expandedUsageKeys.value.filter((item) => item !== key)
    : [...expandedUsageKeys.value, key]
}
/** 按用户筛选：ID / 用户名 / 姓名 / 邮箱都能搜，后端做子串匹配 */
const usageUserKeyword = ref('')
let usageSearchTimer: ReturnType<typeof setTimeout> | null = null

/** 展示用的「有信息量的那个名字」：姓名 > 用户名 > 裸 ID */
function usageUserLabel(row: UsageRow): string {
  return row.user_real_name || row.user_name || row.user_id || '未知用户'
}

/** 鼠标悬停看完整身份，列表里只放一眼能区分的部分 */
function usageUserTitle(row: UsageRow): string {
  const parts = [
    row.user_id ? `ID: ${row.user_id}` : '',
    row.user_name ? `用户名: ${row.user_name}` : '',
    row.user_real_name ? `姓名: ${row.user_real_name}` : '',
    row.user_email ? `邮箱: ${row.user_email}` : '',
  ]
  return parts.filter(Boolean).join('\n')
}

/** `null` = 全部凭据；传 id 就是只看这一条 */
const usageSecretId = ref<string | null>(null)
const usageTab = ref<'detail' | 'stats'>('detail')

async function loadUsageDetail() {
  usageLoading.value = true
  try {
    const res = await axios.get('/api/portal/secrets/usage', {
      params: {
        limit: 100,
        // 一次工具调用注入多个参数会写多条流水，聚合后才是「一次调用一行」
        grouped: 'true',
        ...(usageSecretId.value ? { secret_id: usageSecretId.value } : {}),
        ...(usageUserKeyword.value.trim()
          ? { user_keyword: usageUserKeyword.value.trim() }
          : {}),
      },
    })
    usageRows.value = res.data?.data || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载使用记录失败', 'err')
  } finally {
    usageLoading.value = false
  }
}

/** 输入即搜，但要防抖：每敲一个字就发一次请求会把明细接口刷爆 */
function scheduleUsageSearch() {
  if (usageSearchTimer) clearTimeout(usageSearchTimer)
  usageSearchTimer = setTimeout(() => {
    usageSearchTimer = null
    void loadUsageDetail()
  }, 300)
}

function switchUsageTab(tab: 'detail' | 'stats') {
  usageTab.value = tab
  // 统计面板自己拉数据（`:secret-id` 变了会重新加载），这里只管明细
  if (tab === 'detail') void loadUsageDetail()
}

function changeUsageSecret() {
  if (usageTab.value === 'detail') void loadUsageDetail()
}

/** 不传 id 就是全局；卡片上的「使用记录」会传自己那条 */
async function openUsage(secretId: string | null = null) {
  usageOpen.value = true
  usageSecretId.value = secretId
  usageTab.value = 'detail'
  await loadUsageDetail()
}

// ── 编辑元数据 ──────────────────────────────────────────────────────
// 后端 `PATCH /{id}` 一直支持改名称 / 场景 / 域名白名单 / 分类 / 备注，
// 但前端没有入口——配错白名单只能删了重建，而删掉会连带清掉所有绑定和授权，
// 重建后还得重新绑一遍。
const editOpen = ref(false)
const editBusy = ref(false)
const editTarget = ref<SystemSecret | null>(null)
const editForm = ref({
  name: '',
  allowed_scenarios: [] as string[],
  allowed_hosts: '',
  category: '',
  remark: '',
  expires_at: '',
})

function openEdit(item: SystemSecret) {
  editTarget.value = item
  editForm.value = {
    name: item.name,
    allowed_scenarios: [...(item.allowed_scenarios || [])],
    allowed_hosts: (item.allowed_hosts || []).join(', '),
    category: item.category || '',
    remark: item.remark || '',
    expires_at: isoToLocalDateTimeInput(item.expires_at),
  }
  editOpen.value = true
}

function toggleEditScene(key: string) {
  const list = editForm.value.allowed_scenarios
  const idx = list.indexOf(key)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(key)
}

async function saveEdit() {
  const target = editTarget.value
  if (!target || editBusy.value) return
  editBusy.value = true
  try {
    const hosts = editForm.value.allowed_hosts
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
    await axios.patch(`/api/portal/secrets/${target.id}`, {
      name: editForm.value.name.trim() || target.name,
      allowed_scenarios: editForm.value.allowed_scenarios,
      allowed_hosts: hosts,
      category: editForm.value.category.trim() || null,
      remark: editForm.value.remark.trim() || null,
      expires_at: localDateTimeInputToIso(editForm.value.expires_at),
    })
    showToast('已保存', 'ok')
    editOpen.value = false
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '保存失败', 'err')
  } finally {
    editBusy.value = false
  }
}

// ── 轮换值 ──────────────────────────────────────────────────────────
// 编辑弹窗里写着「要换值请用轮换」，但这里原来没有这个按钮——系统级凭据
// 是全平台共用的，不能改值就只能删了重建，连带清掉所有绑定和授权。
const rotateOpen = ref(false)
const rotateBusy = ref(false)
const rotateTarget = ref<SystemSecret | null>(null)
const rotateValue = ref('')
const rotateFormat = ref<'text' | 'json'>('text')
const rotateTemplate = ref<string | null>(null)
const rotateSecretFields = ref<string[]>([])
const rotateValid = ref(false)
/**
 * 轮换弹窗要铺出来的「原结构骨架」（只有键与类型，没有值）。
 *
 * 值不可读，所以没有它时弹窗只能给一张空表，用户得凭记忆默写键名——错一个字母，
 * 别处 `${secret:标识.字段}` 的引用就静默失效。骨架由后端从既有值算出来。
 */
const rotateInitialJson = ref('')
const rotateStructureLoading = ref(false)
/** 结构读失败时的说明：不阻塞轮换，退回空表 + 提示 */
const rotateStructureError = ref('')
const rotateExpiresAt = ref('')
const rotateClearExpires = ref(false)

/**
 * 打开轮换弹窗，并尽量把「原来的键」摆出来。
 *
 * 结构化凭据的键集合就是结构：轮换只该换值，不该让用户重新默写一遍键名。
 * 走 `/admin/{id}/structure` 而不是 `/fields`——后者按「自己 + 被授权」判可见性，
 * 管理员轮换自己没被授权的那条会 404，而管理页恰恰要维护全部系统级凭据。
 */
async function openRotate(item: SystemSecret) {
  rotateTarget.value = item
  rotateValue.value = ''
  rotateFormat.value = item.value_format === 'json' ? 'json' : 'text'
  rotateTemplate.value = item.value_template || null
  rotateSecretFields.value = []
  rotateValid.value = false
  rotateExpiresAt.value = ''
  rotateClearExpires.value = false
  rotateInitialJson.value = ''
  rotateStructureError.value = ''
  rotateOpen.value = true

  if (item.value_format !== 'json') return
  rotateStructureLoading.value = true
  try {
    const res = await axios.get(`/api/portal/secrets/admin/${item.id}/structure`)
    const skeleton = res.data?.data?.skeleton
    // 换了目标就不回填：慢请求回来时用户可能已经关掉又开了另一条
    if (rotateTarget.value?.id !== item.id) return
    if (skeleton && typeof skeleton === 'object') {
      rotateInitialJson.value = JSON.stringify(skeleton, null, 2)
    }
  } catch {
    if (rotateTarget.value?.id !== item.id) return
    // 拿不到结构不拦着轮换，只是退回「自己填键名」，并且说明白
    rotateStructureError.value = '没能读出原来的字段结构，请自己填写字段名（键名要与原来一致）'
  } finally {
    if (rotateTarget.value?.id === item.id) rotateStructureLoading.value = false
  }
}

async function submitRotate() {
  const target = rotateTarget.value
  if (!target || !rotateValid.value || rotateBusy.value) return
  rotateBusy.value = true
  try {
    await axios.post(`/api/portal/secrets/${target.id}/rotate`, {
      value: rotateValue.value,
      // 旧值保留 7 天：正在跑的任务不会在下一分钟突然失败
      overlap_days: 7,
      expires_at: rotateClearExpires.value ? null : localDateTimeInputToIso(rotateExpiresAt.value),
      clear_expires_at: rotateClearExpires.value,
      value_format: rotateFormat.value,
      // 沿用原模板：结构化凭据换成单值等于把结构丢了，摘要也会跟着失效
      value_template: rotateFormat.value === 'json' ? rotateTemplate.value : null,
      value_secret_fields: rotateFormat.value === 'json' ? rotateSecretFields.value : [],
    })
    rotateOpen.value = false
    rotateTarget.value = null
    rotateValue.value = ''
    showToast('已轮换。旧值保留 7 天，正在跑的任务不会中断', 'ok')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '轮换失败', 'err')
  } finally {
    rotateBusy.value = false
  }
}

async function openAudit(secretId = '', secretKey = '') {
  auditSecretId.value = secretId
  auditSecretKey.value = secretKey
  auditKeyword.value = ''
  auditActions.value = []
  auditOpen.value = true
  await loadAudit()
}

let auditTimer: ReturnType<typeof setTimeout> | null = null
/** 输入即搜要防抖：每敲一个字打一次请求，滚动时会明显卡 */
function scheduleAuditSearch() {
  if (auditTimer) clearTimeout(auditTimer)
  auditTimer = setTimeout(() => void loadAudit(), 250)
}

function toggleAuditAction(action: string) {
  auditActions.value = auditActions.value.includes(action)
    ? auditActions.value.filter((item) => item !== action)
    : [...auditActions.value, action]
  void loadAudit()
}

/** 退出「只看这一条」，回到全局列表（筛选条件一起清） */
function clearAuditScope() {
  auditSecretId.value = ''
  auditSecretKey.value = ''
  auditKeyword.value = ''
  auditActions.value = []
  void loadAudit()
}

async function loadAudit() {
  auditLoading.value = true
  try {
    const params: Record<string, unknown> = { limit: 100 }
    if (auditSecretId.value) params.secret_id = auditSecretId.value
    const keyword = auditKeyword.value.trim()
    if (keyword) params.keyword = keyword
    // 逗号分隔而不是数组：axios 默认发 action[]=，FastAPI 的 List 查询只认重复键
    if (auditActions.value.length) params.action = auditActions.value.join(',')
    const res = await axios.get('/api/portal/secrets/audit', { params })
    auditRows.value = res.data?.data || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载变更记录失败', 'err')
  } finally {
    auditLoading.value = false
  }
}

function openCreate() {
  form.value = {
    secret_key: '',
    name: '',
    value: '',
    secret_type: 'credential',
    allowed_scenarios: ['mcp'],
    allowed_hosts: '',
    expires_at: '',
    value_format: 'text',
    value_template: null,
    value_secret_fields: [],
  }
  valueValid.value = false
  createStep.value = 1
  showCreate.value = true
}

// 纯空白不算填了；但保存时用的仍是原值，不 trim —— JSON / PEM 的缩进要保住
const canLeaveStep1 = () => Boolean(form.value.secret_key.trim() && valueValid.value)

function nextCreateStep() {
  if (createStep.value === 1 && !canLeaveStep1()) {
    return showToast('标识和凭据值是必填的', 'err')
  }
  if (createStep.value < 3) createStep.value += 1
}

function prevCreateStep() {
  if (createStep.value > 1) createStep.value -= 1
}

function toggleScene(key: string) {
  const idx = form.value.allowed_scenarios.indexOf(key)
  if (idx >= 0) form.value.allowed_scenarios.splice(idx, 1)
  else form.value.allowed_scenarios.push(key)
}

async function submitCreate() {
  if (!form.value.secret_key.trim()) return showToast('请填写凭据标识', 'err')
  if (!form.value.value.trim()) return showToast('请填写凭据值', 'err')
  // 「下一步」已经拦过一次，但提交是唯一的写入口，不能只靠上一步的按钮状态
  if (!valueValid.value) return showToast('凭据值还没填完整，回到第 1 步检查标红的字段', 'err')
  createBusy.value = true
  try {
    await axios.post('/api/portal/secrets/admin', {
      secret_key: form.value.secret_key.trim(),
      name: form.value.name.trim() || form.value.secret_key.trim(),
      value: form.value.value,
      secret_type: form.value.secret_type,
      allowed_scenarios: form.value.allowed_scenarios,
      allowed_hosts: form.value.allowed_hosts
        .split(/[,，\s]+/)
        .map((h) => h.trim())
        .filter(Boolean),
      expires_at: localDateTimeInputToIso(form.value.expires_at),
      // 单值档不写模板：后端会忽略，但这里先清掉，避免库里留下误导性的标识
      value_format: form.value.value_format,
      value_template: form.value.value_format === 'json' ? form.value.value_template : null,
      value_secret_fields:
        form.value.value_format === 'json' ? form.value.value_secret_fields : [],
    })
    showCreate.value = false
    showToast('已创建。记得授权，否则没有人能用它')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '创建失败', 'err')
  } finally {
    createBusy.value = false
  }
}

// ── 授权 / 撤销 ───────────────────────────────────────────────────────
const grantTarget = ref<SystemSecret | null>(null)
const grantBusy = ref(false)
// 'user:7' / 'dept:3' —— 类型和 id 一起传，提交时不用再单独维护 grantee_type
const grantSelected = ref<string[]>([])
/**
 * 「授权给全体用户」。放在选择器外面而不是塞进 ``grantSelected``：
 * ``UserDeptPicker`` 是通用组件，``user:7`` / ``dept:3`` 的键格式到处在用，
 * 为这一个特例改它的键格式会牵连别的调用方。
 */
const grantToAll = ref(false)

const grantedKeys = computed(() =>
  (grantTarget.value?.grants || []).map((g) => `${g.grantee_type}:${g.grantee_id}`),
)

/** 已经授权给全体了，就不用再逐个挑人 */
const grantedToAll = computed(() =>
  (grantTarget.value?.grants || []).some((g) => g.grantee_type === 'all'),
)

function openGrant(item: SystemSecret) {
  grantTarget.value = item
  grantSelected.value = []
  grantToAll.value = false
}

async function submitGrant() {
  if (!grantTarget.value) return
  if (!grantSelected.value.length && !grantToAll.value) return
  grantBusy.value = true
  try {
    // 后端一次授权一个对象；批量授权在前端串起来，失败的那些要如实报出来，
    // 不能笼统说「已授权」——管理员会以为全都成了。
    const failed: string[] = []
    if (grantToAll.value) {
      try {
        await axios.post(`/api/portal/secrets/admin/${grantTarget.value.id}/grants`, {
          grantee_type: 'all',
          grantee_id: '',
        })
      } catch {
        failed.push('全体用户')
      }
    }
    for (const key of grantSelected.value) {
      const [granteeType, granteeId] = key.split(':', 2)
      if (!granteeType || !granteeId) continue
      try {
        await axios.post(`/api/portal/secrets/admin/${grantTarget.value.id}/grants`, {
          grantee_type: granteeType,
          grantee_id: granteeId,
        })
      } catch {
        failed.push(key)
      }
    }
    const count = grantSelected.value.length + (grantToAll.value ? 1 : 0)
    grantTarget.value = null
    if (failed.length) showToast(`${failed.length} 个对象授权失败，其余已授权`, 'err')
    else showToast(`已授权 ${count} 个对象`)
    await load()
  } finally {
    grantBusy.value = false
  }
}

async function revoke(item: SystemSecret, grant: Grant) {
  try {
    await axios.delete(`/api/portal/secrets/admin/${item.id}/grants`, {
      params: { grantee_type: grant.grantee_type, grantee_id: grant.grantee_id },
    })
    showToast('已撤销授权')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '撤销失败', 'err')
  }
}

// ── 强制停用 ──────────────────────────────────────────────────────────
const disableTarget = ref<SystemSecret | null>(null)
const disableBusy = ref(false)

async function submitDisable() {
  if (!disableTarget.value) return
  disableBusy.value = true
  try {
    await axios.post(`/api/portal/secrets/admin/${disableTarget.value.id}/disable`)
    disableTarget.value = null
    showToast('已停用。所有绑定立即失效，恢复后即可重新生效')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '停用失败', 'err')
  } finally {
    disableBusy.value = false
  }
}

// ── 恢复启用 ──────────────────────────────────────────────────────────
//
// 停用必须可逆，否则管理员不敢用它止血：误停用一条全体共用的凭据之后，
// 平台里没有路径能恢复，只能重建——而重建意味着所有绑定都要重新做一遍。
// 后端一直有 `POST /admin/{id}/status`，这里把入口接上。
const enableBusyId = ref('')

async function enableSecret(item: SystemSecret) {
  if (enableBusyId.value) return
  enableBusyId.value = item.id
  try {
    await axios.post(`/api/portal/secrets/admin/${item.id}/status`, { status: 'active' })
    showToast('已恢复启用，原有授权与绑定继续有效')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '恢复失败', 'err')
  } finally {
    enableBusyId.value = ''
  }
}

// ── 删除 ──────────────────────────────────────────────────────────────
//
// 走的是个人页同一个端点 `DELETE /secrets/{id}`：系统级只要求调用者是管理员
// （`center.delete_secret` 里判 `actor_is_admin`），没必要再造一条 /admin 路由。
// 后端会显式清 `sys_secret_grants` 与 `sys_secret_bindings`（两张表没有外键，
// 没人替我们级联），使用流水与变更记录保留。
const deleteTarget = ref<SystemSecret | null>(null)
const deleteBusy = ref(false)

async function submitDelete() {
  if (!deleteTarget.value) return
  deleteBusy.value = true
  try {
    await axios.delete(`/api/portal/secrets/${deleteTarget.value.id}`)
    deleteTarget.value = null
    showToast('已删除，授权与绑定一并清除')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '删除失败', 'err')
  } finally {
    deleteBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <!-- 统计：四个数字原来全靠文字区分，扫一眼只看到「4 / 4 / 4 / 0」。
         加图标是为了靠形状就能分出「总量 / 可用 / 授权对象 / 待处理」，底色跟着
         语义走（琥珀 = 需要注意）；副标题说的是**口径**，省得每次都要口头解释。 -->
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div class="flex items-center gap-3 rounded-xl border border-gray-200 bg-white p-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-600">
          <svg class="h-[18px] w-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
            <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
            <path d="m3 12 9 4.5 9-4.5" />
            <path d="m3 16.5 9 4.5 9-4.5" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] text-gray-500">系统级凭据</p>
          <p class="mt-0.5 text-2xl font-semibold text-gray-900">{{ stats.total }}</p>
          <p class="mt-0.5 text-[10.5px] text-gray-400">凭证 {{ stats.credentials }} · 变量 {{ stats.variables }}</p>
        </div>
      </div>
      <div class="flex items-center gap-3 rounded-xl border border-gray-200 bg-white p-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
          <svg class="h-[18px] w-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="9" />
            <path d="m8.5 12 2.5 2.5 4.5-5" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] text-gray-500">生效中</p>
          <p class="mt-0.5 text-2xl font-semibold text-emerald-600">{{ stats.active }}</p>
          <p class="mt-0.5 text-[10.5px] text-gray-400">
            {{ stats.disabled ? `${stats.disabled} 条已停用` : '没有停用的' }}
          </p>
        </div>
      </div>
      <div class="flex items-center gap-3 rounded-xl border border-gray-200 bg-white p-3">
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700">
          <svg class="h-[18px] w-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
            <path d="M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20" />
            <circle cx="9" cy="7" r="3.2" />
            <path d="M22 20v-1.5a4 4 0 0 0-3-3.87" />
            <path d="M16.5 4.2a3.2 3.2 0 0 1 0 5.6" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] text-gray-500">授权对象</p>
          <p class="mt-0.5 text-2xl font-semibold text-gray-900">{{ stats.grantees }}</p>
          <p class="mt-0.5 truncate text-[10.5px] text-gray-400">
            用户 {{ stats.grantUsers }} · 部门 {{ stats.grantDepts }} · 全体 {{ stats.grantAll }}
          </p>
        </div>
      </div>
      <div class="flex items-center gap-3 rounded-xl border border-gray-200 bg-white p-3">
        <span
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
          :class="stats.attention ? 'bg-amber-50 text-amber-600' : 'bg-gray-100 text-gray-400'"
        >
          <svg class="h-[18px] w-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
            <path d="M10.3 3.8 2.5 17.2A2 2 0 0 0 4.2 20h15.6a2 2 0 0 0 1.7-2.8L13.7 3.8a2 2 0 0 0-3.4 0Z" />
            <path d="M12 9v4" />
            <path d="M12 17h.01" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] text-gray-500">需要注意</p>
          <p
            class="mt-0.5 text-2xl font-semibold"
            :class="stats.attention ? 'text-amber-600' : 'text-gray-900'"
          >{{ stats.attention }}</p>
          <p class="mt-0.5 truncate text-[10.5px] text-gray-400">
            {{ stats.unbound }} 条未绑定 · {{ stats.expiring }} 条 7 天内到期
          </p>
        </div>
      </div>
    </div>

    <div
      v-if="stats.noGrant && showNoGrantHint"
      class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800"
    >
      <p class="min-w-0 flex-1">
        有 <strong>{{ stats.noGrant }}</strong> 条系统级凭据处于「生效中但没有任何授权」。它们对除管理员外的所有人不可见，也不会有任何调用能用到它们。
      </p>
      <button
        type="button"
        class="shrink-0 rounded p-0.5 text-amber-500 hover:bg-amber-100 hover:text-amber-800"
        title="关闭提示（不再显示）"
        aria-label="关闭未授权提示"
        @click="dismissNoGrantHint"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- 工具栏 -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm text-gray-600">
        系统级凭据对<strong>被授权的用户</strong>可见可用；未授权的连名称都不会出现。
      </p>
      <!-- 窄屏：搜索框独占一行，「新建」再独占一行；中间一行放视图切换与两个记录入口。
           以前整块用一个不换行的 flex，六个控件挤在一行里，中文按钮被压成一列竖排字。 -->
      <div class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索"
          class="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none sm:w-52"
        />
        <!-- 视图切换：卡片看得全，列表扫得快 -->
        <div class="flex shrink-0 items-center rounded-lg border border-gray-300 bg-white p-0.5" role="group" aria-label="视图切换">
          <button
            type="button"
            class="rounded-md p-1.5"
            :class="viewMode === 'card' ? 'bg-slate-900 text-white' : 'text-gray-400 hover:text-gray-700'"
            title="卡片视图"
            aria-label="卡片视图"
            :aria-pressed="viewMode === 'card'"
            @click="setViewMode('card')"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z" />
            </svg>
          </button>
          <button
            type="button"
            class="rounded-md p-1.5"
            :class="viewMode === 'list' ? 'bg-slate-900 text-white' : 'text-gray-400 hover:text-gray-700'"
            title="列表视图"
            aria-label="列表视图"
            :aria-pressed="viewMode === 'list'"
            @click="setViewMode('list')"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>
        <button
          type="button"
          class="shrink-0 whitespace-nowrap rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          @click="openAudit()"
        >
          变更记录
        </button>
        <button
          type="button"
          class="shrink-0 whitespace-nowrap rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          @click="openUsage()"
        >
          使用记录
        </button>
        <!-- 窄屏独占一行：它是这一页唯一的写操作，挤在中间行会被压到四个字换行 -->
        <button
          type="button"
          class="w-full shrink-0 whitespace-nowrap rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 sm:w-auto"
          @click="openCreate"
        >
          新建系统凭据
        </button>
      </div>
    </div>

    <!-- 状态筛选：指标卡只说「有几条要处理」，这一排说的是**哪几条**。
         计数和筛选共用同一个判定函数，所以 chip 上的数字一定等于点进去的条数。 -->
    <div v-if="items.length" class="flex flex-wrap items-center gap-1.5">
      <button
        v-for="filter in STATUS_FILTERS"
        :key="filter.key"
        type="button"
        class="rounded-full px-2.5 py-1 text-[11.5px] ring-1 ring-inset transition-colors"
        :class="statusFilter === filter.key
          ? 'bg-slate-900 text-white ring-slate-900'
          : filter.warn && filter.count
            ? 'bg-amber-50 text-amber-700 ring-amber-200 hover:bg-amber-100'
            : 'bg-white text-gray-600 ring-gray-200 hover:bg-gray-50'"
        @click="statusFilter = filter.key"
      >
        {{ filter.label }}
        <span
          class="ml-1"
          :class="statusFilter === filter.key ? 'text-white/65' : 'text-gray-400'"
        >{{ filter.count }}</span>
      </button>
      <button
        v-if="statusFilter !== 'all'"
        type="button"
        class="rounded-full px-2 py-1 text-[11.5px] text-gray-400 hover:text-gray-700"
        @click="statusFilter = 'all'"
      >清除筛选</button>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="py-12 text-center text-sm text-gray-400">加载中…</div>
    <div
      v-else-if="!filtered.length"
      class="rounded-xl border border-dashed border-gray-300 py-14 text-center text-sm text-gray-500"
    >
      <template v-if="items.length">
        没有匹配的凭据——换个关键字，或点上面的「全部」看所有记录
      </template>
      <template v-else>还没有系统级凭据</template>
    </div>
    <template v-else>
    <!-- 三列：动作收进 ⋯ 之后，卡片头部只剩「名称 + 徽标 + ⋯」，
         每列 ~440px（1440 屏）仍放得下摘要与授权 chips。窄屏退两列 / 一列。 -->
    <div v-if="viewMode === 'card'" class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <div
        v-for="item in filtered"
        :key="item.id"
        class="rounded-xl border border-gray-200 bg-white p-3.5 transition-shadow hover:shadow-md"
        :class="['border-l-4', cardAccent(item)]"
      >
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5">
              <span class="truncate font-medium text-gray-900" :title="item.name">{{ item.name }}</span>
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-1.5">
              <SecretKeyChip :secret-key="item.secret_key" />
              <SecretFormatBadge :format="item.value_format" :template="item.value_template" size="sm" />
            </div>
            <p v-if="item.value_format === 'json'" class="mt-1 break-all font-mono text-[11px] text-indigo-700">
              {{ item.value_summary || '（无摘要）' }}
            </p>
            <p v-else class="mt-1 font-mono text-[11px] text-gray-400">{{ item.value_masked }}</p>
            <div class="mt-1.5 flex flex-wrap gap-1">
              <span
                v-for="scene in visibleScenarios(item.allowed_scenarios)"
                :key="scene"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                {{ SCENE_LABEL[scene] || scene }}
              </span>
              <span v-if="item.allowed_hosts.length" class="rounded bg-sky-50 px-1.5 py-0.5 text-[11px] text-sky-700">
                {{ item.allowed_hosts.join(', ') }}
              </span>
            </div>
          </div>

          <!-- 卡片上不再平铺动作：三列布局里每列只有 ~440px，七八个按钮会把
               名称与摘要挤成两行。改成与列表一致的 ⋯ 菜单（同一个组件，动作清单
               不会各长各的）。恢复「停用 / 恢复」互斥、删除排在最后都由它保证。 -->
          <div class="flex shrink-0 items-center gap-1">
            <span
              v-if="item.failed_7d"
              class="rounded px-1.5 py-0.5 text-[10.5px] font-medium"
              :class="'bg-rose-100 text-rose-700'"
              :title="`近 7 天有 ${item.failed_7d} 次调用失败——点「使用记录」看具体原因`"
            >{{ item.failed_7d }} 次失败</span>
            <span
              v-if="expiryBadge(item)"
              class="rounded px-1.5 py-0.5 text-[10.5px] font-medium"
              :class="isExpired(item) ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-800'"
              :title="expiryHint(item)"
            >{{ expiryBadge(item) }}</span>
            <span
              v-else
              class="rounded px-1.5 py-0.5 text-[10.5px] font-medium"
              :class="item.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
            >
              {{ item.status === 'active' ? '生效中' : '已停用' }}
            </span>
            <SecretRowActions
              :active="item.status === 'active'"
              :name="item.name"
              @usage="openUsage(item.id)"
              @audit="openAudit(item.id, item.secret_key)"
              @edit="openEdit(item)"
              @rotate="openRotate(item)"
              @grant="openGrant(item)"
              @disable="disableTarget = item"
              @enable="enableSecret(item)"
              @delete="deleteTarget = item"
            />
          </div>
        </div>

        <!-- 授权范围 -->
        <div class="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-gray-100 pt-2.5">
          <span class="text-[11px] text-gray-400">授权范围</span>
          <span v-if="!item.grants.length" class="text-[11px] text-amber-600">尚未授权给任何人</span>
          <span
            v-for="grant in visibleGrants(item)"
            :key="grant.id"
            class="inline-flex items-center gap-1 rounded-md bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700"
            :title="grantHint(grant)"
          >
            {{ grantLabel(grant) }}
            <button
              type="button"
              class="text-gray-400 hover:text-rose-600"
              title="撤销授权"
              @click="revoke(item, grant)"
            >
              ×
            </button>
          </span>
          <!-- 授权给几十个人是常态，全摊开会把卡片撑成一面墙；
               +N 展开、再点收起，完整名单也能悬停看 title -->
          <button
            v-if="hiddenGrantCount(item)"
            type="button"
            class="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-500 hover:border-gray-300 hover:bg-gray-50 hover:text-gray-800"
            :title="`展开其余 ${hiddenGrantCount(item)} 个授权对象`"
            @click="toggleGrantGroup(item.id)"
          >
            +{{ hiddenGrantCount(item) }}
          </button>
          <button
            v-else-if="item.grants.length > GRANT_PREVIEW_COUNT"
            type="button"
            class="rounded-md px-1.5 py-0.5 text-[11px] text-gray-400 hover:text-gray-700"
            title="收起授权名单"
            @click="toggleGrantGroup(item.id)"
          >
            收起
          </button>
        </div>

        <!-- 绑定目标：按消费者类型分组——
             混在一起看不出「这条凭据被哪几类地方用」，uuid 和原始槽位也对使用者没意义 -->
        <div v-if="item.bindings.length" class="mt-2 space-y-1">
          <div
            v-for="group in groupBindings(item.bindings)"
            :key="group.type"
            class="flex flex-wrap items-center gap-1"
          >
            <span
              class="w-16 shrink-0 rounded px-1 py-0.5 text-[10px] font-medium"
              :class="group.style"
            >{{ group.label }}</span>
            <span
              v-for="binding in shownBindingItems(item.id, group)"
              :key="binding.id"
              class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-gray-700"
              :class="binding.rule_shadow === 'fail'
                ? 'bg-gray-50 opacity-60 ring-1 ring-dashed ring-gray-300'
                : 'bg-gray-50'"
              :title="`${group.label}：${binding.consumer_name || binding.consumer_id}\n槽位：${binding.slot}\n绑定者：${binding.is_global ? '全局' : (binding.bound_by_name || binding.bound_by_user_id)}${bindingShadowHint(binding)}`"
            >
              <span class="font-medium">{{ binding.consumer_name || binding.consumer_id }}</span>
              <code class="font-mono text-gray-500">{{ slotLabel(binding) }}</code>
              <span class="rounded bg-white px-1 text-[10px] text-gray-500">
                {{ binding.is_global ? '全局' : (binding.bound_by_name || binding.bound_by_user_id) }}
              </span>
            </span>
            <!-- 超出预览条数才给展开：多出来的本身就说明「这条凭据被一堆参数位用着」 -->
            <button
              v-if="group.items.length > BINDING_PREVIEW_COUNT"
              type="button"
              class="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-gray-100 hover:text-gray-700"
              :title="isBindingGroupExpanded(item.id, group.type) ? '收起' : `展开其余 ${group.items.length - BINDING_PREVIEW_COUNT} 条`"
              @click="toggleBindingGroup(item.id, group.type)"
            >
              {{ isBindingGroupExpanded(item.id, group.type)
                ? '收起'
                : `+${group.items.length - BINDING_PREVIEW_COUNT}` }}
            </button>
          </div>
        </div>

        <!-- 没绑定时给的是下一步，而不是一句「已绑定 0 处」：后者只是陈述，
             后果要用户自己推断——而后果是「这条凭据当前不会被任何调用用到」。 -->
        <p
          v-if="!(item.binding_count || 0)"
          class="mt-2 flex items-start gap-1 text-[11px] text-amber-700"
        >
          <svg class="mt-[2px] h-3 w-3 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="M12 9v4m0 4h.01" stroke-linecap="round" />
            <circle cx="12" cy="12" r="9" />
          </svg>
          <span>还没绑到任何消费者——绑上工具参数后才会真正被调用</span>
        </p>

        <!-- 绑定与使用：建完不等于在用，这几格就是「在跑还是摆着」。
             两列排布是因为三列布局下横排一行放不下，而竖排又太占高度。
             创建时间与最近调用并列：一个不变、一个常变，不该混在同一行里读。 -->
        <div
          class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-gray-100 pt-2.5 text-[11px] text-gray-500"
        >
          <span>
            已绑定
            <b class="font-medium" :class="(item.binding_count || 0) ? 'text-gray-700' : 'text-amber-600'">
              {{ item.binding_count || 0 }}
            </b>
            处
          </span>
          <span>
            近 7 天调用 <b class="font-medium text-gray-700">{{ item.usage_7d || 0 }}</b> 次
            <span v-if="item.failed_7d" class="text-rose-600">· 失败 {{ item.failed_7d }}</span>
          </span>
          <span>最近一次 {{ shortTime(item.last_used_at) }}</span>
          <span :title="item.last_rotated_at ? `上次轮换：${shortTime(item.last_rotated_at)}` : '这条还没轮换过'">
            创建于 {{ shortTime(item.created_at) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 列表视图：一行一条。卡片上平铺的四个动作在这里收进 ⋯，
         否则「名称」和「使用情况」会被按钮挤掉，而这两列才是扫列表要看的 -->
    <div v-else class="overflow-x-auto rounded-xl border border-gray-200 bg-white">
      <table class="w-full min-w-[920px] border-collapse text-sm">
        <thead>
          <tr class="border-b border-gray-100 bg-gray-50/70 text-left text-[11px] font-medium text-gray-500">
            <th class="px-3 py-2">名称</th>
            <th class="w-20 px-3 py-2">状态</th>
            <th class="px-3 py-2">场景</th>
            <th class="w-28 px-3 py-2">授权范围</th>
            <th class="w-20 px-3 py-2">绑定</th>
            <th class="w-36 px-3 py-2">近 7 天</th>
            <th class="w-12 px-3 py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="item in filtered" :key="item.id" class="hover:bg-gray-50/70">
            <td class="px-3 py-2.5">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="font-medium text-gray-900">{{ item.name }}</span>
                <SecretKeyChip :secret-key="item.secret_key" />
                <SecretFormatBadge :format="item.value_format" :template="item.value_template" size="sm" />
              </div>
              <!-- 结构化凭据没有「可看的前缀」，这里显示后端挑出的非敏感摘要 -->
              <p v-if="item.value_format === 'json'" class="mt-0.5 break-all font-mono text-[11px] text-indigo-700">
                {{ item.value_summary || '（无摘要）' }}
              </p>
              <p v-else class="mt-0.5 font-mono text-[11px] text-gray-400">{{ item.value_masked }}</p>
            </td>
            <td class="px-3 py-2.5">
              <span
                class="rounded px-1.5 py-0.5 text-[11px] font-medium"
                :class="item.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
              >
                {{ item.status === 'active' ? '生效中' : '已停用' }}
              </span>
              <!-- 卡片上已经有的两个异常徽标，列表里也要有：
                   否则同一个库换个体视图看，「这条有问题」就消失了 -->
              <div v-if="expiryBadge(item)" class="mt-1">
                <span
                  class="rounded px-1 py-0.5 text-[10px] font-medium"
                  :class="isExpired(item) ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-800'"
                  :title="expiryHint(item)"
                >{{ expiryBadge(item) }}</span>
              </div>
              <div v-if="item.failed_7d" class="mt-1">
                <span class="rounded bg-rose-100 px-1 py-0.5 text-[10px] font-medium text-rose-700">
                  失败 {{ item.failed_7d }}
                </span>
              </div>
            </td>
            <td class="px-3 py-2.5">
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="scene in visibleScenarios(item.allowed_scenarios)"
                  :key="scene"
                  class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
                >
                  {{ SCENE_LABEL[scene] || scene }}
                </span>
                <span
                  v-if="!visibleScenarios(item.allowed_scenarios).length"
                  class="text-[11px] text-amber-600"
                >无场景</span>
              </div>
            </td>
            <td class="px-3 py-2.5">
              <span
                class="text-[11.5px]"
                :class="item.grant_count ? 'text-gray-700' : 'text-amber-600'"
                :title="grantTitle(item)"
              >{{ grantSummary(item) }}</span>
            </td>
            <td class="px-3 py-2.5">
              <span
                class="text-[11.5px]"
                :class="item.binding_count ? 'text-gray-700' : 'text-amber-600'"
                :title="bindingTitle(item)"
              >{{ item.binding_count || 0 }} 处</span>
            </td>
            <td class="px-3 py-2.5 text-[11.5px] text-gray-600">
              <span>{{ item.usage_7d || 0 }} 次</span>
              <span v-if="item.failed_7d" class="text-amber-600"> · 失败 {{ item.failed_7d }}</span>
              <span class="mt-0.5 block text-[11px] text-gray-400">{{ shortTime(item.last_used_at) }}</span>
            </td>
            <td class="px-3 py-2.5 text-right">
              <SecretRowActions
                :active="item.status === 'active'"
                :name="item.name"
                @usage="openUsage(item.id)"
                @audit="openAudit(item.id, item.secret_key)"
                @edit="openEdit(item)"
                @rotate="openRotate(item)"
                @grant="openGrant(item)"
                @disable="disableTarget = item"
                @enable="enableSecret(item)"
                @delete="deleteTarget = item"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>

    <div v-if="showPlaintextHint" class="flex items-start gap-2">
      <p class="min-w-0 flex-1 text-[11px] leading-relaxed text-gray-400">
        管理员也读不到凭据明文——这不是权限设置，是平台没有这个能力。要更换值只能轮换；确实要作废这条，可以删除后重建（会连带清掉授权与绑定）。
      </p>
      <button
        type="button"
        class="shrink-0 rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
        title="关闭提示（不再显示）"
        aria-label="关闭明文说明"
        @click="dismissPlaintextHint"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- 新建：三步向导（与个人中心同一套结构） -->
    <Teleport to="body">
      <div
        v-if="showCreate"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="showCreate = false"
      >
        <!--
          尺寸写死，不跟着内容走。
          原来宽度和高度都是自适应的：切「自由输入 / 结构化（模板）」时，模板卡片区
          一出现，弹窗就从 max-w-xl 跳到 max-w-3xl、高度也跟着窜一截，刚对准的
          「下一步」按钮会从鼠标底下跑掉。
          宽度按宽的那个固定（结构化要两列卡片）；高度 `h` 给死，再用 `max-h`
          兜住小屏——`max-height` 优先于 `height`，所以屏幕够高就是 760px，
          不够就退到 100vh-120px。中间的滚动区本来就是 `flex-1 overflow-y-auto`，
          内容多了滚动，而不是把弹窗撑破。
        -->
        <div
          class="flex h-[760px] max-h-[calc(100vh-120px)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        >
          <div class="border-b border-gray-100 px-5 py-3">
            <h3 class="flex items-center gap-1.5 text-base font-semibold text-gray-900">
              <span class="text-slate-900">+</span> 新建系统级凭据
            </h3>
            <p class="mt-0.5 text-xs text-gray-500">
              三步：说清楚是什么 → 划清楚能去哪 → 确认；建完还要授权，否则没有人能用
            </p>
          </div>

          <div class="flex items-center gap-0 bg-gray-50 px-5 py-2.5">
            <template v-for="(label, index) in ['基本信息', '注入范围', '确认']" :key="label">
              <div
                class="flex items-center gap-1.5 text-[11.5px] font-semibold"
                :class="createStep === index + 1 ? 'text-slate-900' : (createStep > index + 1 ? 'text-emerald-700' : 'text-gray-400')"
              >
                <span
                  class="flex h-[19px] w-[19px] items-center justify-center rounded-full text-[10px]"
                  :class="createStep === index + 1 ? 'bg-slate-900 text-white' : (createStep > index + 1 ? 'bg-emerald-600 text-white' : 'bg-gray-200 text-gray-500')"
                >{{ createStep > index + 1 ? '✓' : index + 1 }}</span>
                {{ label }}
              </div>
              <span
                v-if="index < 2"
                class="mx-2.5 h-0.5 w-6 rounded"
                :class="createStep > index + 1 ? 'bg-emerald-500' : 'bg-gray-300'"
              />
            </template>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <!-- 步骤 1 -->
            <div v-if="createStep === 1" class="space-y-3">
              <label class="block">
                <span class="text-xs font-medium text-gray-700">凭据标识 <span class="text-rose-500">*</span></span>
                <input
                  v-model="form.secret_key"
                  placeholder="corp.wecom.secret"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-slate-500 focus:outline-none"
                />
                <span class="mt-1 block text-[11px] text-gray-400">
                  授权对象看到的就是这个标识；小写字母、数字、点、下划线、连字符
                </span>
              </label>
              <label class="block">
                <span class="text-xs font-medium text-gray-700">名称</span>
                <input
                  v-model="form.name"
                  placeholder="留空则用标识"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                />
              </label>
              <div>
                <span class="text-xs font-medium text-gray-700">类型</span>
                <div class="mt-1 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    class="rounded-lg border px-3 py-2 text-left text-xs"
                    :class="form.secret_type === 'credential' ? 'border-violet-400 bg-violet-50' : 'border-gray-200'"
                    @click="form.secret_type = 'credential'"
                  >
                    <span class="block font-medium text-gray-900">凭证</span>
                    <span class="text-[11px] text-gray-500">Token / 密码，建完不可读</span>
                  </button>
                  <button
                    type="button"
                    class="rounded-lg border px-3 py-2 text-left text-xs"
                    :class="form.secret_type === 'variable' ? 'border-sky-400 bg-sky-50' : 'border-gray-200'"
                    @click="form.secret_type = 'variable'"
                  >
                    <span class="block font-medium text-gray-900">变量</span>
                    <span class="text-[11px] text-gray-500">租户号 / 地址，被授权者可复制</span>
                  </button>
                </div>
              </div>
              <CredentialValueEditor
                v-model="form.value"
                v-model:format="form.value_format"
                v-model:template="form.value_template"
                v-model:secretFields="form.value_secret_fields"
                v-model:valid="valueValid"
                :secret-type="form.secret_type"
              />
            </div>

            <!-- 步骤 2 -->
            <div v-else-if="createStep === 2" class="space-y-3">
              <div>
                <span class="text-xs font-medium text-gray-700">允许注入到哪些场景 <span class="text-rose-500">*</span></span>
                <div class="mt-1 space-y-1.5">
                  <button
                    v-for="scene in CREATE_SCENES"
                    :key="scene.key"
                    type="button"
                    :disabled="!scene.wired"
                    class="flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors"
                    :class="scene.wired
                      ? (form.allowed_scenarios.includes(scene.key) ? 'border-slate-400 bg-slate-50' : 'border-gray-200 hover:border-gray-300')
                      : 'cursor-not-allowed border-dashed border-gray-200 bg-gray-50 opacity-70'"
                    @click="scene.wired && toggleScene(scene.key)"
                  >
                    <span
                      class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] text-white"
                      :class="form.allowed_scenarios.includes(scene.key) ? 'border-slate-900 bg-slate-900' : 'border-gray-300 bg-white'"
                    >{{ form.allowed_scenarios.includes(scene.key) ? '✓' : '' }}</span>
                    <span class="min-w-0">
                      <span class="block text-xs font-medium" :class="scene.wired ? 'text-gray-900' : 'text-gray-400'">
                        {{ scene.label }}
                        <span v-if="!scene.wired" class="ml-1 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-normal text-amber-700">暂未接入</span>
                      </span>
                      <span class="mt-0.5 block text-[11px] leading-snug" :class="scene.wired ? 'text-gray-500' : 'text-amber-600'">
                        {{ scene.desc }}
                      </span>
                    </span>
                  </button>
                </div>
              </div>
              <label class="block">
                <span class="text-xs font-medium text-gray-700">域名白名单</span>
                <input
                  v-model="form.allowed_hosts"
                  placeholder="qyapi.weixin.qq.com"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-slate-500 focus:outline-none"
                />
                <span class="mt-1 block text-[11px] text-gray-400">
                  逗号分隔，支持 <code>*.</code> 通配子域。留空 = 只允许平台内部目标。
                </span>
              </label>
              <div>
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-gray-700">有效期截止（可选，本地时间）</span>
                  <button
                    v-if="form.expires_at"
                    type="button"
                    class="text-[11px] text-blue-600 hover:underline"
                    @click="form.expires_at = ''"
                  >设为长期有效</button>
                </div>
                <input
                  v-model="form.expires_at"
                  type="datetime-local"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                />
                <span class="mt-1 block text-[11px] text-gray-400">
                  留空表示长期有效；过期后调用方会拒绝注入并保留记录。
                </span>
              </div>
              <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                <b>建完不等于能用</b>：系统级凭据还要在列表里单独授权给用户、部门或全体用户，否则没有任何人能取到它。
              </div>
            </div>

            <!-- 步骤 3 -->
            <div v-else class="space-y-3">
              <dl class="divide-y divide-gray-100 rounded-lg border border-gray-200 text-xs">
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">标识</dt>
                  <dd class="font-mono text-gray-900">{{ form.secret_key || '—' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">名称</dt>
                  <dd class="text-gray-900">{{ form.name || form.secret_key || '—' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">类型</dt>
                  <dd class="text-gray-900">{{ form.secret_type === 'credential' ? '凭证（建完不可读）' : '变量（被授权者可复制）' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">场景</dt>
                  <dd class="flex flex-wrap gap-1">
                    <template v-if="visibleScenarios(form.allowed_scenarios).length">
                      <span
                        v-for="key in visibleScenarios(form.allowed_scenarios)"
                        :key="key"
                        class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-700"
                        :title="sceneDescOf(key)"
                      >{{ SCENE_LABEL[key] || key }}</span>
                    </template>
                    <span v-else class="text-amber-600">一个都没勾 —— 建完也取不到</span>
                  </dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">域名</dt>
                  <dd class="font-mono text-gray-900">{{ form.allowed_hosts || '仅平台内部' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">有效期</dt>
                  <dd class="text-gray-900">{{ formatLocalDateTimeSummary(form.expires_at) }}</dd>
                </div>
              </dl>
              <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                <b>点创建就回不去了</b>：值会被加密保存，你和其他管理员都再看不到明文。
                创建后还要在列表里授权给具体的人、部门或全体用户。
              </div>
            </div>
          </div>

          <div class="flex items-center gap-2 border-t border-gray-100 px-5 py-3">
            <span class="text-[11px] text-gray-400">
              <template v-if="createStep === 1">下一步配置注入范围</template>
              <template v-else-if="createStep === 2">还剩一步：确认</template>
              <template v-else>创建后记得授权</template>
            </span>
            <div class="ml-auto flex items-center gap-2">
              <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="showCreate = false">
                取消
              </button>
              <button
                v-if="createStep > 1"
                type="button"
                class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
                @click="prevCreateStep"
              >上一步</button>
              <button
                v-if="createStep < 3"
                type="button"
                class="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
                @click="nextCreateStep"
              >下一步</button>
              <button
                v-else
                type="button"
                :disabled="createBusy"
                class="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
                @click="submitCreate"
              >{{ createBusy ? '创建中…' : '确认创建' }}</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

<!-- 授权 -->
    <Teleport to="body">
          <div
            v-if="grantTarget"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="grantTarget = null"
          >
            <div class="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">授权凭据</h3>
              <p class="mt-1 text-xs text-gray-500">
                <code>{{ grantTarget.secret_key }}</code> 授权给谁。被授权的人才能看见并使用它。
              </p>
              <label
                class="mt-3 flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors"
                :class="grantedToAll
                  ? 'border-gray-200 bg-gray-50 cursor-not-allowed'
                  : grantToAll
                    ? 'border-blue-300 bg-blue-50'
                    : 'border-gray-200 bg-white hover:bg-gray-50'"
              >
                <input
                  v-model="grantToAll"
                  type="checkbox"
                  :disabled="grantedToAll"
                  class="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                />
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-gray-800">授权给全体用户</span>
                  <span class="mt-0.5 block text-[11px] leading-relaxed text-gray-500">
                    <template v-if="grantedToAll">
                      这条凭据已经对全体开放了。
                    </template>
                    <template v-else>
                      包括现在和以后新增的所有部门与成员。适合平台统一采购、人人共用的账号；
                      按人计费的服务不要用它。
                    </template>
                  </span>
                </span>
              </label>

              <div class="mt-3" :class="grantToAll || grantedToAll ? 'opacity-50 pointer-events-none' : ''">
                <UserDeptPicker
                  v-model="grantSelected"
                  :mode="'both'"
                  :disabled-keys="grantedKeys"
                />
              </div>

              <div class="mt-4 flex items-center gap-2">
                <span class="text-[11px] text-gray-400">
                  <template v-if="grantToAll">已选：全体用户</template>
                  <template v-else-if="grantSelected.length">已选 {{ grantSelected.length }} 个</template>
                  <template v-else>授权给部门后，部门内所有成员都能用</template>
                </span>
                <div class="ml-auto flex items-center gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="grantTarget = null">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="grantBusy || (!grantSelected.length && !grantToAll)"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                  @click="submitGrant"
                >
                  {{ grantBusy ? '授权中…' : '确认授权' }}
                </button>
                </div>
              </div>
            </div>
          </div>
    </Teleport>

    <!-- 强制停用 -->
    <Teleport to="body">
          <div
            v-if="disableTarget"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="disableTarget = null"
          >
            <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">强制停用？</h3>
              <p class="mt-1 text-xs leading-relaxed text-gray-500">
                <code>{{ disableTarget.secret_key }}</code> 会立即对所有使用者失效；<strong>可以恢复</strong>，恢复后原有授权与绑定继续有效。
                这是疑似泄漏时的止损手段；正常轮换请让创建者操作。
              </p>
              <div class="mt-4 flex justify-end gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="disableTarget = null">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="disableBusy"
                  class="rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-60"
                  @click="submitDisable"
                >
                  {{ disableBusy ? '停用中…' : '确认停用' }}
                </button>
              </div>
            </div>
          </div>
    </Teleport>

    <!-- 删除确认 -->
    <Teleport to="body">
          <div
            v-if="deleteTarget"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="deleteTarget = null"
          >
            <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">删除凭据？</h3>
              <p class="mt-1 text-xs leading-relaxed text-gray-500">
                <code>{{ deleteTarget.secret_key }}</code> 会被<strong>彻底删除</strong>，这条记录无法找回。
                <template v-if="deleteTarget.grant_count">
                  它的 <b class="text-rose-600">{{ deleteTarget.grant_count }}</b> 条授权会一并清除；
                </template>
                <template v-if="deleteTarget.binding_count">
                  它当前绑在 <b class="text-rose-600">{{ deleteTarget.binding_count }}</b> 个地方，这些绑定会一并清除，相关调用立刻开始失败；
                </template>
                使用记录与变更记录会保留，能查到「谁在什么时候删了它」。
              </p>
              <p class="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-700">
                只是想让它暂时失效、过阵子再用，请改用「强制停用」——那个可以恢复，删除不行。
              </p>
              <div class="mt-4 flex justify-end gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="deleteTarget = null">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="deleteBusy"
                  class="rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-60"
                  @click="submitDelete"
                >
                  {{ deleteBusy ? '删除中…' : '确认删除' }}
                </button>
              </div>
            </div>
          </div>
    </Teleport>

    <!-- 变更记录弹框 -->
    <Teleport to="body">
      <div
        v-if="auditOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="auditOpen = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="border-b border-gray-100 px-5 py-3">
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <h3 class="flex flex-wrap items-baseline gap-1.5 text-base font-semibold text-gray-900">
                  变更记录
                  <span
                    v-if="auditSecretKey"
                    class="truncate rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] font-normal text-gray-600"
                  >{{ auditSecretKey }}</span>
                </h3>
                <p class="mt-0.5 text-xs text-gray-500">
                  {{ auditSecretKey ? '只看这条凭据：谁在什么时候改了什么。' : '谁在什么时候对哪条凭据做了什么。' }}
                  不记值——凭据删除后记录仍保留。
                </p>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                @click="auditOpen = false"
              >✕</button>
            </div>

            <!-- 搜索：一个框搜三处——凭据标识、操作人、detail 里的槽位与规则。
                 记录一多，靠眼睛在一屏几十条里捞「谁把这条规则删了」是不现实的。 -->
            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <div class="relative w-full min-w-0 sm:w-64">
                <input
                  v-model="auditKeyword"
                  type="text"
                  placeholder="搜凭据标识 / 操作人 / 槽位或规则"
                  spellcheck="false"
                  class="w-full rounded-lg border border-gray-300 py-1 pl-2.5 pr-7 text-xs focus:border-primary focus:outline-none"
                  @input="scheduleAuditSearch"
                />
                <button
                  v-if="auditKeyword"
                  type="button"
                  class="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1 text-sm leading-none text-gray-400 hover:text-gray-600"
                  title="清空关键字"
                  @click="auditKeyword = ''; loadAudit()"
                >×</button>
              </div>
              <button
                v-if="auditSecretId"
                type="button"
                class="whitespace-nowrap rounded-lg border border-gray-300 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
                title="退出「只看这一条」，回到全局变更记录"
                @click="clearAuditScope"
              >只看本条 · 查看全部</button>
            </div>

            <!-- 动作筛选：按后果分组的那套文案直接拿来当筛选项，别另列一份 -->
            <div class="mt-1.5 flex flex-wrap items-center gap-1">
              <button
                v-for="action in AUDIT_ACTIONS"
                :key="action"
                type="button"
                class="rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset transition-colors"
                :class="auditActions.includes(action)
                  ? 'bg-slate-900 text-white ring-slate-900'
                  : 'bg-white text-gray-600 ring-gray-200 hover:bg-gray-50'"
                @click="toggleAuditAction(action)"
              >{{ secretActionLabel(action) }}</button>
              <button
                v-if="auditActions.length"
                type="button"
                class="rounded-full px-2 py-0.5 text-[11px] text-gray-400 hover:text-gray-600"
                @click="auditActions = []; loadAudit()"
              >清空</button>
            </div>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-3">
            <p v-if="auditLoading" class="py-8 text-center text-xs text-gray-400">加载中…</p>
            <p v-else-if="!auditRows.length" class="py-8 text-center text-xs text-gray-400">
              <template v-if="auditFiltered">
                没有匹配的变更记录——换个关键字，或清掉动作筛选再看
              </template>
              <template v-else>还没有变更记录</template>
            </p>
            <div v-else class="space-y-1.5">
              <div
                v-for="row in auditRows"
                :key="row.id"
                class="rounded-lg bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-600"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <code class="text-gray-800">{{ row.secret_key || '已删除' }}</code>
                  <span
                    class="rounded px-1.5 py-0.5 font-medium ring-1 ring-inset"
                    :class="secretActionStyle(row.action)"
                  >{{ secretActionLabel(row.action) }}</span>
                  <!-- 谁做的：原来直接摊 actor_id，管理员看不出是谁 -->
                  <span
                    v-if="row.actor_label || row.actor_id"
                    class="text-gray-600"
                    :title="row.actor_id ? `操作人 ID：${row.actor_id}` : ''"
                  >{{ row.actor_label || row.actor_id }}</span>
                  <span class="ml-auto text-gray-400">{{ shortTime(row.created_at) }}</span>
                </div>
                <p
                  v-if="row.detail_text"
                  class="mt-0.5 truncate font-mono text-[10px] text-gray-400"
                  :title="row.detail_text"
                >{{ row.detail_text }}</p>
              </div>
            </div>
          </div>

          <div class="flex items-center justify-end border-t border-gray-100 px-5 py-3">
            <button
              type="button"
              class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              @click="auditOpen = false"
            >关闭</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 轮换值弹框 -->
    <Teleport to="body">
      <div
        v-if="rotateOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="rotateOpen = false"
      >
        <div
          class="flex max-h-[calc(100vh-120px)] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
          :class="rotateFormat === 'json' ? 'max-w-2xl' : 'max-w-md'"
        >
          <div class="border-b border-gray-100 px-5 py-3">
            <h3 class="text-base font-semibold text-gray-900">轮换凭据值</h3>
            <p class="mt-0.5 text-xs text-gray-500">
              <code class="rounded bg-gray-100 px-1 py-0.5">{{ rotateTarget?.secret_key }}</code>
              的所有绑定方会同时用上新值。
            </p>
            <p v-if="rotateTarget?.value_format === 'json'" class="mt-1 text-[11px] text-blue-700">
              这条是结构化凭据（{{ credentialTemplateLabel(rotateTarget?.value_template) }}），
              结构已锁定：轮换只换值、不换模板。换模板会让别处已写好的
              <code class="rounded bg-blue-50 px-1">${secret:标识.字段}</code> 引用全部失效。
            </p>
          </div>

          <div class="min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-4">
            <p v-if="rotateStructureLoading" class="text-[11px] text-gray-500">正在读取原来的字段结构…</p>
            <p v-else-if="rotateStructureError" class="text-[11px] text-amber-700">{{ rotateStructureError }}</p>
            <CredentialValueEditor
              v-model="rotateValue"
              v-model:format="rotateFormat"
              v-model:template="rotateTemplate"
              v-model:secretFields="rotateSecretFields"
              v-model:valid="rotateValid"
              :secret-type="rotateTarget?.secret_type || 'credential'"
              :lock-structure="true"
              :initial-json="rotateInitialJson"
            />
            <div class="space-y-1.5 rounded-lg border border-gray-200 bg-gray-50/70 p-3">
              <div class="flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-gray-700">新凭据有效期（可选，本地时间）</span>
                <span class="text-[11px] text-gray-500">
                  原有效期：{{ rotateTarget?.expires_at ? shortTime(rotateTarget.expires_at) : '长期有效' }}
                </span>
              </div>
              <input
                v-model="rotateExpiresAt"
                type="datetime-local"
                :disabled="rotateClearExpires"
                class="block w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs focus:border-primary focus:outline-none disabled:bg-gray-100 disabled:text-gray-400"
                @input="rotateClearExpires = false"
              />
              <label class="flex items-center gap-1.5 text-[11px] text-gray-600">
                <input
                  v-model="rotateClearExpires"
                  type="checkbox"
                  class="rounded border-gray-300 text-primary focus:ring-primary"
                  @change="rotateClearExpires && (rotateExpiresAt = '')"
                />
                <span>清除原截止时间，转为长期有效</span>
              </label>
              <p class="text-[11px] text-gray-400">
                留空且不勾选时默认沿用原截止时间（若原凭据已过期或将在 7 天过渡窗内到期，则自动清为长期有效）。
              </p>
            </div>
            <p class="text-[11px] leading-relaxed text-amber-700">
              旧值会保留 <b>7 天</b>作为回退窗口——正在跑的任务不会立刻失败；
              7 天后自动失效。想不等这 7 天就让旧值彻底不起作用，可以先把这条凭据强制停用（可恢复），确认新值跑通再恢复。
            </p>
          </div>

          <div class="flex items-center justify-end gap-2 border-t border-gray-100 px-5 py-3">
            <button
              type="button"
              class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              @click="rotateOpen = false"
            >取消</button>
            <button
              type="button"
              :disabled="rotateBusy || !rotateValid"
              class="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
              @click="submitRotate"
            >{{ rotateBusy ? '提交中…' : '轮换' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 编辑元数据弹框 -->
    <Teleport to="body">
      <div
        v-if="editOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="editOpen = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="border-b border-gray-100 px-5 py-3">
            <h3 class="text-base font-semibold text-gray-900">编辑凭据</h3>
            <p class="mt-0.5 text-xs text-gray-500">
              <code class="rounded bg-gray-100 px-1 py-0.5">{{ editTarget?.secret_key }}</code>
              的值不能改——要换值请用「轮换」，那样会留下记录。
            </p>
          </div>

          <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
            <label class="block">
              <span class="text-xs font-medium text-gray-700">名称</span>
              <input
                v-model="editForm.name"
                class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
                placeholder="显示名"
              />
            </label>

            <div>
              <span class="text-xs font-medium text-gray-700">允许的场景</span>
              <p class="mt-0.5 text-[11px] text-gray-400">
                一个都不勾等于「哪里都用不了」——注入时会被判为场景不符。
              </p>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <button
                  v-for="scene in CREATE_SCENES"
                  :key="scene.key"
                  type="button"
                  :disabled="!scene.wired"
                  :title="scene.desc"
                  class="rounded-md px-2 py-1 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  :class="editForm.allowed_scenarios.includes(scene.key)
                    ? 'bg-primary text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'"
                  @click="toggleEditScene(scene.key)"
                >{{ scene.label }}</button>
              </div>
            </div>

            <label class="block">
              <span class="text-xs font-medium text-gray-700">域名白名单</span>
              <p class="mt-0.5 text-[11px] text-gray-400">
                逗号分隔。<b>留空只允许平台内部目标</b>（localhost / 内网），
                公网地址必须写进来，或用 <code>*</code> 表示不限。
              </p>
              <input
                v-model="editForm.allowed_hosts"
                class="mt-1 block w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-primary"
                placeholder="api.example.com, *.example.com"
              />
            </label>

            <div class="grid grid-cols-2 gap-3">
              <label class="block">
                <span class="text-xs font-medium text-gray-700">分类</span>
                <input
                  v-model="editForm.category"
                  class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
                  placeholder="可选"
                />
              </label>
              <label class="block">
                <span class="text-xs font-medium text-gray-700">备注</span>
                <input
                  v-model="editForm.remark"
                  class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
                  placeholder="可选"
                />
              </label>
            </div>

            <div>
              <div class="flex items-center justify-between">
                <span class="text-xs font-medium text-gray-700">有效期截止（本地时间）</span>
                <button
                  v-if="editForm.expires_at"
                  type="button"
                  class="text-[11px] text-blue-600 hover:underline"
                  @click="editForm.expires_at = ''"
                >设为长期有效</button>
              </div>
              <input
                v-model="editForm.expires_at"
                type="datetime-local"
                class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-primary focus:ring-primary"
              />
              <p class="mt-0.5 text-[11px] text-gray-400">
                留空表示长期有效；到期后运行时会自动拦截并写入失效记录。
              </p>
            </div>
          </div>

          <div class="flex items-center justify-end gap-2 border-t border-gray-100 px-5 py-3">
            <button
              type="button"
              class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              @click="editOpen = false"
            >取消</button>
            <button
              type="button"
              :disabled="editBusy"
              class="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
              @click="saveEdit"
            >{{ editBusy ? '保存中…' : '保存' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 使用记录弹框 -->
    <Teleport to="body">
      <div
        v-if="usageOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="usageOpen = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-gray-100 px-5 py-3">
            <div>
              <h3 class="text-base font-semibold text-gray-900">使用记录</h3>
              <p class="mt-0.5 text-xs text-gray-500">
                每次取值的实际结果，含被拒绝的和没匹配上绑定的——只记「谁要取什么」，不记值本身。
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              @click="usageOpen = false"
            >✕</button>
          </div>

          <div class="flex flex-wrap items-center gap-2 border-b border-gray-100 px-5 py-2">
            <div class="flex rounded-lg bg-gray-100 p-0.5">
              <button
                type="button"
                class="rounded-md px-3 py-1 text-xs font-medium transition-colors"
                :class="usageTab === 'detail' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                @click="switchUsageTab('detail')"
              >明细</button>
              <button
                type="button"
                class="rounded-md px-3 py-1 text-xs font-medium transition-colors"
                :class="usageTab === 'stats' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                @click="switchUsageTab('stats')"
              >统计</button>
            </div>
            <select
              v-model="usageSecretId"
              class="rounded-md border-gray-300 text-xs shadow-sm focus:border-primary focus:ring-primary"
              @change="changeUsageSecret"
            >
              <option :value="null">全部凭据</option>
              <option v-for="item in items" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
            <input
              v-if="usageTab === 'detail'"
              v-model="usageUserKeyword"
              type="search"
              placeholder="按用户筛选（ID / 用户名 / 姓名 / 邮箱）"
              class="w-56 rounded-md border-gray-300 text-xs shadow-sm focus:border-primary focus:ring-primary"
              @input="scheduleUsageSearch"
            />
            <span v-if="usageTab === 'stats'" class="text-[11px] text-gray-400">近 7 天</span>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-3">
            <p v-if="usageLoading" class="py-8 text-center text-xs text-gray-400">加载中…</p>

            <!-- 统计：与管理端/个人端共用同一个组件，避免两边口径分叉 -->
            <SecretUsageStats
              v-else-if="usageTab === 'stats'"
              :secret-id="usageSecretId"
            />

            <p v-else-if="!usageRows.length" class="py-8 text-center text-xs text-gray-400">
              还没有使用记录
            </p>
            <div v-else class="space-y-1.5">
              <div
                v-for="row in usageRows"
                :key="row.id"
                class="rounded-lg bg-gray-50 px-2.5 py-2 text-[11px] text-gray-600"
              >
                <div class="flex flex-wrap items-center gap-2">
                  <code class="text-gray-800">{{ row.secret_key || '已删除' }}</code>
                  <span
                    class="rounded px-1.5 py-0.5 font-medium"
                    :class="row.result === 'success'
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-amber-50 text-amber-700'"
                  >{{ resultLabel(row.result) }}</span>
                  <span class="text-gray-400">{{ SCENE_LABEL[row.scene] || row.scene }}</span>
                  <span
                    v-if="row.user_id"
                    class="text-gray-500"
                    :title="usageUserTitle(row)"
                  >{{ usageUserLabel(row) }}<span
                    v-if="row.user_id"
                    class="ml-1 font-mono text-[10px] text-gray-400"
                  >#{{ row.user_id }}</span><span
                    v-if="row.user_email"
                    class="ml-1 text-[10px] text-gray-400"
                  >{{ row.user_email }}</span></span>
                  <span class="ml-auto shrink-0 text-gray-400">{{ shortTime(row.created_at) }}</span>
                </div>
                <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] text-gray-400">
                  <!-- 一次调用注入多个参数聚合成一行，点开展开看每个参数位 -->
                  <button
                    v-if="(row.slot_count || 1) > 1"
                    type="button"
                    class="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-1.5 py-0.5 font-sans text-[10px] text-gray-600 hover:border-gray-300 hover:bg-gray-50"
                    :title="expandedUsageKeys.includes(row.id) ? '收起参数明细' : '展开看这次注入了哪几个参数'"
                    @click="toggleUsageExpand(row)"
                  >
                    {{ row.slot_count }} 个参数
                    <span class="text-gray-400">{{ expandedUsageKeys.includes(row.id) ? '收起' : '展开' }}</span>
                  </button>
                  <span v-else-if="row.slot">{{ row.slot }}</span>
                  <span v-if="row.failed_count" class="font-sans text-amber-600">{{ row.failed_count }} 项失败</span>
                  <span v-if="row.target_host">{{ row.target_host }}</span>
                  <!-- 回到那次对话才查得动「用户说这条凭据用不了」；trace_id 只够定位单次调用。
                       历史流水这一列是 NULL（注入链路过去没传），所以显式说「无会话」而不是留空。 -->
                  <a
                    v-if="row.conversation_id"
                    :href="`/dashboard/chat?conversation_id=${row.conversation_id}`"
                    target="_blank"
                    rel="noopener"
                    class="text-blue-600 hover:underline"
                    :title="`打开这次会话：${row.conversation_id}`"
                  >会话 {{ String(row.conversation_id).slice(0, 8) }}</a>
                  <span v-else class="text-gray-300" title="这条流水没有记录会话（注入链路在 2026-11-15 之前不传 conversation_id）">无会话</span>
                  <span
                    v-if="row.trace_id"
                    class="text-gray-400"
                    :title="`trace：${row.trace_id}`"
                  >trace {{ String(row.trace_id).slice(0, 8) }}</span>
                </div>
                <div
                  v-if="expandedUsageKeys.includes(row.id) && (row.slots || []).length"
                  class="mt-1 space-y-0.5 border-t border-gray-200/70 pt-1"
                >
                  <div
                    v-for="(detail, index) in row.slots"
                    :key="`${detail.slot}-${index}`"
                    class="flex items-center gap-2 pl-3 font-mono text-[10px] text-gray-500"
                  >
                    <span class="text-gray-300">└</span>
                    <span class="truncate">{{ detail.slot }}</span>
                    <span :class="detail.result === 'success' ? 'text-emerald-600' : 'text-amber-600'">
                      {{ resultLabel(detail.result) }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="flex items-center justify-end border-t border-gray-100 px-5 py-3">
            <button
              type="button"
              class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              @click="usageOpen = false"
            >关闭</button>
          </div>
        </div>
      </div>
    </Teleport>

    <div v-if="toast" class="pointer-events-none fixed bottom-6 left-1/2 z-[60] -translate-x-1/2">
      <div
        class="rounded-xl px-4 py-2 text-sm text-white shadow-lg"
        :class="toast.type === 'err' ? 'bg-rose-600' : 'bg-gray-900'"
      >
        {{ toast.text }}
      </div>
    </div>
  </div>
</template>

<style scoped>
/* textarea 没有 type="password"，掩码只能靠这个；Firefox 不支持，
   那里会退化成明文——所以「提交后不可再查看」这条提示不能省。 */
.masked-value {
  -webkit-text-security: disc;
}
</style>
