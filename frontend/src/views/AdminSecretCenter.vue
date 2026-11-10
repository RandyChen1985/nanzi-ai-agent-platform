<script setup lang="ts">
/**
 * 凭据中心（系统管理 → 凭据中心）。
 *
 * 与个人中心的「我的凭据」共用同一套表和同一个注入层，差别只在管理面：
 * 管理员能看到全部系统级凭据、能授权/撤销、能强制停用。
 *
 * **管理员的权限边界**（不是前端限制，是后端就不给）：
 * - 看不到任何明文——`credential` 建完即不可读，管理员也没有解密接口；
 * - 不能「恢复」被停用的凭据，只能重新创建；
 * - 不能停用个人凭据（那是用户自己的资产）。
 */
import { computed, onMounted, ref } from 'vue'
import SecretUsageStats from '../components/secrets/SecretUsageStats.vue'
import SecretRowActions from '../components/secrets/SecretRowActions.vue'
import { resultLabel } from '../utils/secretLabels'
import UserDeptPicker from '../components/common/UserDeptPicker.vue'
import { useDismissible } from '../utils/dismissible'
import axios from '../utils/axios'
import { shortTime } from '../utils/date'

interface Grant {
  id: string
  grantee_type: 'user' | 'dept' | 'all'
  grantee_id: string
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
  grants: Grant[]
  grant_count: number
  last_rotated_at: string | null
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

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (item) => item.name.toLowerCase().includes(q) || item.secret_key.toLowerCase().includes(q),
  )
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

/** 列表列宽有限，授权范围只给摘要；完整名单挂在 title 里 */
function grantSummary(item: SystemSecret): string {
  if (!item.grants.length) return '未授权'
  if (item.grants.some((grant) => grant.grantee_type === 'all')) return '全体用户'
  return `${item.grants.length} 个对象`
}

function grantTitle(item: SystemSecret): string {
  if (!item.grants.length) return '尚未授权给任何人，除管理员外不可见'
  return item.grants
    .map((grant) =>
      grant.grantee_type === 'all'
        ? '全体用户'
        : `${grant.grantee_type === 'dept' ? '部门' : '用户'} · ${grant.grantee_id}`,
    )
    .join('\n')
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
  const grantees = new Set(
    items.value.flatMap((i) => i.grants.map((g) => `${g.grantee_type}:${g.grantee_id}`)),
  )
  const noGrant = items.value.filter((i) => i.grant_count === 0 && i.status === 'active').length
  return { total: items.value.length, active, grantees: grantees.size, noGrant }
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
})

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
  detail: string | null
  created_at: string
}
const auditRows = ref<AuditRow[]>([])

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
  created_at: string
}
const usageRows = ref<UsageRow[]>([])
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
})

function openEdit(item: SystemSecret) {
  editTarget.value = item
  editForm.value = {
    name: item.name,
    allowed_scenarios: [...(item.allowed_scenarios || [])],
    allowed_hosts: (item.allowed_hosts || []).join(', '),
    category: item.category || '',
    remark: item.remark || '',
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
const revealRotateValue = ref(false)

function openRotate(item: SystemSecret) {
  rotateTarget.value = item
  rotateValue.value = ''
  revealRotateValue.value = false
  rotateOpen.value = true
}

async function submitRotate() {
  const target = rotateTarget.value
  if (!target || !rotateValue.value.trim() || rotateBusy.value) return
  rotateBusy.value = true
  try {
    await axios.post(`/api/portal/secrets/${target.id}/rotate`, {
      value: rotateValue.value,
      // 旧值保留 7 天：正在跑的任务不会在下一分钟突然失败
      overlap_days: 7,
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

async function openAudit() {
  auditOpen.value = true
  auditLoading.value = true
  try {
    const res = await axios.get('/api/portal/secrets/audit', { params: { limit: 100 } })
    auditRows.value = res.data?.data || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载变更记录失败', 'err')
  } finally {
    auditLoading.value = false
  }
}

//: 凭据值默认掩码。JSON / 证书这类多行值需要看清结构，所以要能临时显形。
const revealValue = ref(false)

function openCreate() {
  revealValue.value = false
  form.value = {
    secret_key: '',
    name: '',
    value: '',
    secret_type: 'credential',
    allowed_scenarios: ['mcp'],
    allowed_hosts: '',
  }
  createStep.value = 1
  showCreate.value = true
}

// 纯空白不算填了；但保存时用的仍是原值，不 trim —— JSON / PEM 的缩进要保住
const canLeaveStep1 = () => Boolean(form.value.secret_key.trim() && form.value.value.trim())

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
    showToast('已停用。所有绑定立即失效，且不可恢复')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '停用失败', 'err')
  } finally {
    disableBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <!-- 统计 -->
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div class="rounded-xl border border-gray-200 bg-white p-3">
        <p class="text-[11px] text-gray-500">系统级凭据</p>
        <p class="mt-1 text-2xl font-semibold text-gray-900">{{ stats.total }}</p>
      </div>
      <div class="rounded-xl border border-gray-200 bg-white p-3">
        <p class="text-[11px] text-gray-500">生效中</p>
        <p class="mt-1 text-2xl font-semibold text-emerald-600">{{ stats.active }}</p>
      </div>
      <div class="rounded-xl border border-gray-200 bg-white p-3">
        <p class="text-[11px] text-gray-500">授权对象</p>
        <p class="mt-1 text-2xl font-semibold text-gray-900">{{ stats.grantees }}</p>
      </div>
      <div class="rounded-xl border border-gray-200 bg-white p-3">
        <p class="text-[11px] text-gray-500">未授权</p>
        <p class="mt-1 text-2xl font-semibold" :class="stats.noGrant ? 'text-amber-600' : 'text-gray-900'">
          {{ stats.noGrant }}
        </p>
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
      <div class="flex items-center gap-2">
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索"
          class="w-40 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none sm:w-52"
        />
        <!-- 视图切换：卡片看得全，列表扫得快 -->
        <div class="flex items-center rounded-lg border border-gray-300 bg-white p-0.5" role="group" aria-label="视图切换">
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
          class="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          @click="openAudit"
        >
          变更记录
        </button>
        <button
          type="button"
          class="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          @click="openUsage()"
        >
          使用记录
        </button>
        <button
          type="button"
          class="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
          @click="openCreate"
        >
          新建系统凭据
        </button>
      </div>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="py-12 text-center text-sm text-gray-400">加载中…</div>
    <div
      v-else-if="!filtered.length"
      class="rounded-xl border border-dashed border-gray-300 py-14 text-center text-sm text-gray-500"
    >
      还没有系统级凭据
    </div>
    <template v-else>
    <div v-if="viewMode === 'card'" class="grid gap-3 lg:grid-cols-2">
      <div
        v-for="item in filtered"
        :key="item.id"
        class="rounded-xl border border-gray-200 bg-white p-3.5 transition-shadow hover:shadow-md"
        :class="item.status === 'active'
          ? 'border-l-4 border-l-emerald-400'
          : 'border-l-4 border-l-gray-300'"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-medium text-gray-900">{{ item.name }}</span>
              <code class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">{{ item.secret_key }}</code>
              <span
                class="rounded px-1.5 py-0.5 text-[11px] font-medium"
                :class="item.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
              >
                {{ item.status === 'active' ? '生效中' : '已停用' }}
              </span>
            </div>
            <p class="mt-1 font-mono text-[11px] text-gray-400">{{ item.value_masked }}</p>
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

          <div class="flex shrink-0 items-center gap-1">
            <button
              type="button"
              class="rounded-md px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
              title="只看这条凭据的使用记录"
              @click="openUsage(item.id)"
            >
              使用记录
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
              title="改名称、场景、域名白名单（值只能轮换）"
              @click="openEdit(item)"
            >
              编辑
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
              title="换掉这条凭据的值，旧值保留 7 天"
              @click="openRotate(item)"
            >
              轮换
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
              @click="openGrant(item)"
            >
              授权
            </button>
            <button
              v-if="item.status === 'active'"
              type="button"
              class="rounded-md px-2 py-1 text-xs text-rose-600 hover:bg-rose-50"
              @click="disableTarget = item"
            >
              强制停用
            </button>
          </div>
        </div>

        <!-- 授权范围 -->
        <div class="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-gray-100 pt-2.5">
          <span class="text-[11px] text-gray-400">授权范围</span>
          <span v-if="!item.grants.length" class="text-[11px] text-amber-600">尚未授权给任何人</span>
          <span
            v-for="grant in item.grants"
            :key="grant.id"
            class="inline-flex items-center gap-1 rounded-md bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700"
          >
            <template v-if="grant.grantee_type === 'all'">全体用户</template>
            <template v-else>
              {{ grant.grantee_type === 'dept' ? '部门' : '用户' }} · {{ grant.grantee_id }}
            </template>
            <button
              type="button"
              class="text-gray-400 hover:text-rose-600"
              title="撤销授权"
              @click="revoke(item, grant)"
            >
              ×
            </button>
          </span>
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
              v-for="binding in group.items"
              :key="binding.id"
              class="inline-flex items-center gap-1 rounded bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-700"
              :title="`${group.label}：${binding.consumer_name || binding.consumer_id}\n槽位：${binding.slot}\n绑定者：${binding.is_global ? '全局' : (binding.bound_by_name || binding.bound_by_user_id)}`"
            >
              <span class="font-medium">{{ binding.consumer_name || binding.consumer_id }}</span>
              <code class="font-mono text-gray-500">{{ slotLabel(binding) }}</code>
              <span class="rounded bg-white px-1 text-[10px] text-gray-500">
                {{ binding.is_global ? '全局' : (binding.bound_by_name || binding.bound_by_user_id) }}
              </span>
            </span>
          </div>
        </div>

        <!-- 绑定与使用：建完不等于在用，这两行就是「在跑还是摆着」 -->
        <div
          class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-gray-100 pt-2.5 text-[11px] text-gray-500"
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
          </span>
          <span v-if="item.failed_7d" class="text-amber-600">其中失败 {{ item.failed_7d }} 次</span>
          <span>最近一次 {{ shortTime(item.last_used_at) }}</span>
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
                <code class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">{{ item.secret_key }}</code>
              </div>
              <p class="mt-0.5 font-mono text-[11px] text-gray-400">{{ item.value_masked }}</p>
            </td>
            <td class="px-3 py-2.5">
              <span
                class="rounded px-1.5 py-0.5 text-[11px] font-medium"
                :class="item.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
              >
                {{ item.status === 'active' ? '生效中' : '已停用' }}
              </span>
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
                @edit="openEdit(item)"
                @rotate="openRotate(item)"
                @grant="openGrant(item)"
                @disable="disableTarget = item"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>

    <div v-if="showPlaintextHint" class="flex items-start gap-2">
      <p class="min-w-0 flex-1 text-[11px] leading-relaxed text-gray-400">
        管理员也读不到凭据明文——这不是权限设置，是平台没有这个能力。要更换值只能让创建者轮换，或停用后重建。
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
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
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
              <label class="block">
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-gray-700">凭据值 <span class="text-rose-500">*</span></span>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                    @click="revealValue = !revealValue"
                  >
                    {{ revealValue ? '隐藏' : '显示' }}
                  </button>
                </div>
                <textarea
                  v-model="form.value"
                  :rows="4"
                  spellcheck="false"
                  autocomplete="off"
                  placeholder="短 token 直接粘贴；JSON / PEM 这类多行值可以整段贴进来"
                  class="mt-1 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm leading-relaxed focus:border-slate-500 focus:outline-none"
                  :class="{ 'masked-value': !revealValue }"
                ></textarea>
                <span class="mt-1 block text-[11px] text-amber-600">
                  提交后任何人（包括你）都无法再查看明文，只能停用后重建。多行内容会原样保存，不会被 trim。
                </span>
              </label>
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
                <code>{{ disableTarget.secret_key }}</code> 会立即对所有使用者失效，<strong>不可恢复</strong>。
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

    <!-- 变更记录弹框 -->
    <Teleport to="body">
      <div
        v-if="auditOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="auditOpen = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-gray-100 px-5 py-3">
            <div>
              <h3 class="text-base font-semibold text-gray-900">变更记录</h3>
              <p class="mt-0.5 text-xs text-gray-500">
                谁在什么时候对哪条凭据做了什么。不记值——凭据删除后记录仍保留。
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              @click="auditOpen = false"
            >✕</button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-3">
            <p v-if="auditLoading" class="py-8 text-center text-xs text-gray-400">加载中…</p>
            <p v-else-if="!auditRows.length" class="py-8 text-center text-xs text-gray-400">
              还没有变更记录
            </p>
            <div v-else class="space-y-1.5">
              <div
                v-for="row in auditRows"
                :key="row.id"
                class="flex flex-wrap items-center gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-600"
              >
                <code class="text-gray-800">{{ row.secret_key || '已删除' }}</code>
                <span
                  class="rounded px-1.5 py-0.5 font-medium ring-1 ring-inset"
                  :class="secretActionStyle(row.action)"
                >{{ secretActionLabel(row.action) }}</span>
                <span v-if="row.actor_id" class="text-gray-400">{{ row.actor_id }}</span>
                <span class="ml-auto text-gray-400">{{ shortTime(row.created_at) }}</span>
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
        <div class="flex w-full max-w-md flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="border-b border-gray-100 px-5 py-3">
            <h3 class="text-base font-semibold text-gray-900">轮换凭据值</h3>
            <p class="mt-0.5 text-xs text-gray-500">
              <code class="rounded bg-gray-100 px-1 py-0.5">{{ rotateTarget?.secret_key }}</code>
              的所有绑定方会同时用上新值。
            </p>
          </div>

          <div class="space-y-2 px-5 py-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-gray-700">新值</span>
              <button
                type="button"
                class="text-[11px] text-gray-500 hover:text-gray-800"
                @click="revealRotateValue = !revealRotateValue"
              >{{ revealRotateValue ? '隐藏' : '显示' }}</button>
            </div>
            <textarea
              v-model="rotateValue"
              :rows="3"
              class="block w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-primary focus:ring-primary"
              :class="{ 'masked-value': !revealRotateValue }"
              placeholder="粘贴新的 Token / 密钥"
            ></textarea>
            <p class="text-[11px] leading-relaxed text-amber-700">
              旧值会保留 <b>7 天</b>作为回退窗口——正在跑的任务不会立刻失败；
              7 天后自动失效。想马上作废旧值，轮换后再点「强制停用」重建。
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
              :disabled="rotateBusy || !rotateValue.trim()"
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
                  <span v-if="row.slot">{{ row.slot }}</span>
                  <span v-if="row.target_host">{{ row.target_host }}</span>
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
