<script setup lang="ts">
/**
 * 凭据中心（个人中心 → 我的凭据）。
 *
 * 与原型 `prototypes/2026-11-09-user-credential-center.html` 的视图 1/2/4/6 对应：
 * 列表（双 scope）→ 创建 → 使用记录 → 注入策略。
 *
 * 三条前端也要守住的约束：
 * 1. **不显示明文**：`credential` 永远只渲染 `value_masked`，连「看一眼」的按钮都没有；
 * 2. **创建与轮换都要二次确认**：值一旦提交就再也读不回来，误操作没有后悔药；
 * 3. **系统共享只读**：用户能看见被授权的系统级凭据、能绑定，但不能改不能删。
 */
import { computed, onMounted, ref, watch } from 'vue'
import axios from '../utils/axios'
import { useDismissible } from '../utils/dismissible'
import { shortTime } from '../utils/date'
import SecretUsageStats from '../components/secrets/SecretUsageStats.vue'
import { RESULT_LABEL } from '../utils/secretLabels'
import {
  secretActionLabel,
  secretActionStyle,
} from '../utils/secretAudit'

interface SecretItem {
  id: string
  owner_scope: 'user' | 'system'
  owner_id: string | null
  secret_key: string
  name: string
  secret_type: 'credential' | 'variable'
  category: string | null
  remark: string | null
  status: string
  allowed_scenarios: string[]
  allowed_hosts: string[]
  value_masked: string
  value_preview: string | null
  readable: boolean
  created_at: string | null
  expires_at: string | null
  last_rotated_at: string | null
  rotating_until: string | null
  // 列表接口聚合出来的三个字段，列表页和详情页都要用
  binding_count?: number
  last_used_at?: string | null
  usage_7d?: number
  failed_7d?: number
}

interface UsageRow {
  id: string
  secret_key: string | null
  scene: string
  result: string
  user_id: string | null
  user_name: string | null
  user_real_name: string | null
  user_email: string | null
  target_host: string | null
  consumer_type: string | null
  created_at: string | null
}

// `wired` 表示这条链路上真的有注入代码。`agent` 目前只有常量，
// 勾了也不会发生任何事——界面上必须把这件事说出来，否则用户会以为配好了。
const SCENES = [
  { key: 'mcp', label: 'MCP 服务', hint: '出站请求头', desc: '作为连接头的值，在建立 MCP 连接时注入', wired: true },
  { key: 'api_tool', label: 'API 工具', hint: '请求头 / 参数', desc: '填进工具的请求头，只补没填的位置；手填了真实值的不覆盖', wired: true },
  { key: 'script_env', label: '脚本环境变量', hint: '默认关，需平台 + 个人都开', desc: '以环境变量形式进子进程，脚本里可直接读；stdout 会被指纹扫描', wired: true },
  { key: 'agent', label: '智能体', hint: '暂未接入，勾了也不会注入', desc: '暂未接入，勾了也不会注入', wired: false },
]

// 已废弃的场景：后端常量还留着（历史数据里有这个值），但代码里早就不参与任何
// 判断了。继续显示会让人以为「勾了就生效」——所以展示层统一过滤掉。
const DEPRECATED_SCENES = new Set(['scheduled_task'])


// ── 编辑元数据 ──────────────────────────────────────────────────────
// 和管理端同一个端点。域名白名单配错一个字符时，删掉重建的代价是所有绑定
// 和授权一起没了——而这里要改的只是元数据。
const editOpen = ref(false)
const editBusy = ref(false)
const editForm = ref({
  name: '',
  allowed_scenarios: [] as string[],
  allowed_hosts: '',
  category: '',
  remark: '',
})

function openEdit() {
  const item = selected.value
  if (!item) return
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
  const item = selected.value
  if (!item || editBusy.value) return
  editBusy.value = true
  try {
    const hosts = editForm.value.allowed_hosts
      .split(/[,，\s]+/)
      .map((part) => part.trim())
      .filter(Boolean)
    await axios.patch(`/api/portal/secrets/${item.id}`, {
      name: editForm.value.name.trim() || item.name,
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

// 两条提示都是一次性说明：看完可以关掉，关掉后记住
const { visible: showSecurityHint, dismiss: dismissSecurityHint } = useDismissible('secret-center:security')
const { visible: showUnboundHint, dismiss: dismissUnboundHint } = useDismissible('secret-center:unbound')

const scope = ref<'mine' | 'shared'>('mine')
const keyword = ref('')
const loading = ref(false)
const items = ref<SecretItem[]>([])

const toast = ref<{ text: string; type: 'ok' | 'err' } | null>(null)
function showToast(text: string, type: 'ok' | 'err' = 'ok') {
  toast.value = { text, type }
  window.setTimeout(() => (toast.value = null), 2600)
}

// ── 三栏浏览：左筛选 / 中列表 / 右详情 ──────────────────────────────
//
// 原型里「待配置」指的是「团队工具需要你的个人密钥，你还没填」。我们没有
// 「消费者声明需要凭据」这张表，硬造一个只会是假的；换成本模型里真实存在、
// 同样需要用户动手的那件事：**建好了却没绑到任何地方**——它同样不会生效。
type FilterKind = 'all' | 'unbound' | 'expired' | 'unused90' | 'multibound' | 'category'

const filterKind = ref<FilterKind>('all')
const activeCategory = ref('')
const selectedId = ref('')

const scopedItems = computed(() =>
  items.value.filter((item) =>
    scope.value === 'mine' ? item.owner_scope === 'user' : item.owner_scope === 'system',
  ),
)

const isExpired = (item: SecretItem) =>
  item.status === 'expired' || (!!item.expires_at && new Date(item.expires_at) < new Date())

const isDisabled = (item: SecretItem) => item.status === 'disabled'

const unboundCount = computed(
  () => scopedItems.value.filter((i) => (i.binding_count || 0) === 0 && !isDisabled(i)).length,
)
const expiredCount = computed(() => scopedItems.value.filter(isExpired).length)
const unused90Count = computed(
  () =>
    scopedItems.value.filter((i) => {
      if (!i.last_used_at) return true
      const days = (Date.now() - new Date(i.last_used_at).getTime()) / 86400000
      return days > 90
    }).length,
)
const multiboundCount = computed(
  () => scopedItems.value.filter((i) => (i.binding_count || 0) > 1).length,
)

/** 分类是从数据里长出来的，不是写死的枚举——用户建凭据时填什么这里就出现什么 */
const categoryGroups = computed(() => {
  const map = new Map<string, number>()
  for (const item of scopedItems.value) {
    const key = (item.category || '').trim() || '未分类'
    map.set(key, (map.get(key) || 0) + 1)
  }
  return [...map.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
})

/** 清除全部筛选条件。筛选为空时这是唯一的退路——别让用户卡在一个空列表里。 */
function resetFilters() {
  filterKind.value = 'all'
  activeCategory.value = ''
  keyword.value = ''
}

const filtered = computed(() => {
  let source = scopedItems.value

  if (filterKind.value === 'unbound') source = source.filter((i) => (i.binding_count || 0) === 0)
  else if (filterKind.value === 'expired') source = source.filter(isExpired)
  else if (filterKind.value === 'multibound') source = source.filter((i) => (i.binding_count || 0) > 1)
  else if (filterKind.value === 'unused90') {
    source = source.filter((i) => {
      if (!i.last_used_at) return true
      return (Date.now() - new Date(i.last_used_at).getTime()) / 86400000 > 90
    })
  } else if (filterKind.value === 'category') {
    source = source.filter((i) => ((i.category || '').trim() || '未分类') === activeCategory.value)
  }

  const q = keyword.value.trim().toLowerCase()
  if (!q) return source
  return source.filter(
    (item) =>
      item.name.toLowerCase().includes(q) ||
      item.secret_key.toLowerCase().includes(q) ||
      (item.category || '').toLowerCase().includes(q),
  )
})

const counts = computed(() => ({
  mine: items.value.filter((i) => i.owner_scope === 'user').length,
  shared: items.value.filter((i) => i.owner_scope === 'system').length,
}))

/** 选中的那条；筛选把它筛掉了就退回第一条，避免右侧显示一条列表里看不到的凭据 */
const selected = computed<SecretItem | null>(() => {
  const hit = filtered.value.find((i) => i.id === selectedId.value)
  return hit || filtered.value[0] || null
})

async function load() {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/secrets', { params: { scope: 'all' } })
    items.value = res.data?.data?.items || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载凭据失败', 'err')
  } finally {
    loading.value = false
  }
}

// ── 创建 ──────────────────────────────────────────────────────────────
const showCreate = ref(false)
// 新建向导的三步：说清楚是什么 → 划清楚能去哪 → 确认不可逆的部分。
// 做成一步会变成一屏十几个字段，用户填到一半就忘了自己在建什么。
const createStep = ref(1)
const createBusy = ref(false)
const form = ref({
  secret_key: '',
  name: '',
  value: '',
  secret_type: 'credential' as 'credential' | 'variable',
  allowed_scenarios: ['mcp'] as string[],
  allowed_hosts: '',
  category: '',
  remark: '',
})

//: 凭据值默认掩码。JSON / 证书这类多行值需要看清结构，所以要能临时显形。
const revealValue = ref(false)
const revealRotateValue = ref(false)

function openCreate() {
  revealValue.value = false
  form.value = {
    secret_key: '',
    name: '',
    value: '',
    secret_type: 'credential',
    allowed_scenarios: ['mcp'],
    allowed_hosts: '',
    category: '',
    remark: '',
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
  const list = form.value.allowed_scenarios
  const idx = list.indexOf(key)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(key)
}

async function submitCreate() {
  if (!form.value.secret_key.trim()) return showToast('请填写凭据标识', 'err')
  if (!form.value.value.trim()) return showToast('请填写凭据值', 'err')
  createBusy.value = true
  try {
    await axios.post('/api/portal/secrets', {
      secret_key: form.value.secret_key.trim(),
      name: form.value.name.trim() || form.value.secret_key.trim(),
      value: form.value.value,
      secret_type: form.value.secret_type,
      allowed_scenarios: form.value.allowed_scenarios,
      allowed_hosts: form.value.allowed_hosts
        .split(/[,，\s]+/)
        .map((h) => h.trim())
        .filter(Boolean),
      category: form.value.category.trim() || null,
      remark: form.value.remark.trim() || null,
    })
    showCreate.value = false
    showToast('已创建。值已加密保存，之后任何人（包括管理员）都读不回来')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '创建失败', 'err')
  } finally {
    createBusy.value = false
  }
}

// ── 轮换 ──────────────────────────────────────────────────────────────
const rotateTarget = ref<SecretItem | null>(null)
const rotateValue = ref('')
const rotateBusy = ref(false)

function openRotate(item: SecretItem) {
  rotateTarget.value = item
  rotateValue.value = ''
  revealRotateValue.value = false
}

async function submitRotate() {
  if (!rotateTarget.value || !rotateValue.value) return
  rotateBusy.value = true
  try {
    await axios.post(`/api/portal/secrets/${rotateTarget.value.id}/rotate`, {
      value: rotateValue.value,
      overlap_days: 7,
    })
    rotateTarget.value = null
    rotateValue.value = ''
    showToast('已轮换。旧值保留 7 天，正在跑的任务不会中断')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '轮换失败', 'err')
  } finally {
    rotateBusy.value = false
  }
}

// ── 删除 ──────────────────────────────────────────────────────────────
const deleteTarget = ref<SecretItem | null>(null)
const deleteBusy = ref(false)

async function submitDelete() {
  if (!deleteTarget.value) return
  deleteBusy.value = true
  try {
    await axios.delete(`/api/portal/secrets/${deleteTarget.value.id}`)
    deleteTarget.value = null
    showToast('已删除')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '删除失败', 'err')
  } finally {
    deleteBusy.value = false
  }
}

// ── 变量取值 ──────────────────────────────────────────────────────────
//
// 只有 `variable` 类型能读明文：这类东西（租户号、接口地址）本身就是要填到别处去
// 的，读不到等于没用。`credential` 走的是同一个接口但后端会 403——能力上就不存在。
const revealOpen = ref(false)
const revealTarget = ref<SecretItem | null>(null)
const revealBusy = ref(false)
const revealedValue = ref('')

async function openReveal(item: SecretItem) {
  revealTarget.value = item
  revealedValue.value = ''
  revealOpen.value = true
  revealBusy.value = true
  try {
    const res = await axios.get(`/api/portal/secrets/${item.id}/value`)
    revealedValue.value = res.data?.data?.value ?? ''
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '读取失败', 'err')
    revealOpen.value = false
  } finally {
    revealBusy.value = false
  }
}

async function copyRevealed() {
  if (!revealedValue.value) return
  try {
    await navigator.clipboard.writeText(revealedValue.value)
    showToast('已复制')
  } catch {
    showToast('复制失败，请手动选中复制', 'err')
  }
}

// ── 绑定总览 ──────────────────────────────────────────────────────────
//
// 「这条凭据被绑到哪了」。没有这个视图时，用户只能去每个 API 工具、每个 MCP 服务、
// 每个脚本里翻，翻不完也不知道有没有漏。反查接口按 secret_id 走同一条 `/bindings`。
const CONSUMER_LABEL: Record<string, string> = {
  api_tool: 'API 工具',
  mcp_server: 'MCP 服务',
  script_tool: '脚本工具',
  agent: '智能体',
}

// ── 使用记录 ──────────────────────────────────────────────────────────
// 弹框，不是页面底部的折叠区：那一块要滚到底才看得见，而且会随列表长度把整页
// 撑长；「谁什么时候用了我的凭据」是想起来才查一次的事，弹出来看完就关更合适。
const usageOpen = ref(false)
/** 「这些凭据会被用在哪里」——这层信息原来只在代码里，界面上看不到 */
const usageHelpOpen = ref(false)
const usageLoading = ref(false)
const usageRows = ref<UsageRow[]>([])

// 「谁用了」和「谁改了」是两个问题，放两个 tab。合成一张表的话，查轮换记录
// 得在几万条调用里翻。
type RecordTab = 'usage' | 'stats' | 'audit'
const recordTab = ref<RecordTab>('usage')
type AuditRow = {
  id: string
  secret_key: string | null
  action: string
  actor_id: string | null
  detail: string | null
  created_at: string
}
const auditRows = ref<AuditRow[]>([])

async function loadRecords(tab: RecordTab) {
  // 统计面板自己拉数据（`:secret-id` 变了会重新加载），这里不代劳
  if (tab === 'stats') return
  usageLoading.value = true
  try {
    const url = tab === 'usage' ? '/api/portal/secrets/usage' : '/api/portal/secrets/audit'
    const res = await axios.get(url, { params: { limit: 50 } })
    if (tab === 'usage') usageRows.value = res.data?.data || []
    else auditRows.value = res.data?.data || []
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载记录失败', 'err')
  } finally {
    usageLoading.value = false
  }
}

async function openUsage() {
  usageOpen.value = true
  recordTab.value = 'usage'
  await loadRecords('usage')
}

async function switchRecordTab(tab: RecordTab) {
  recordTab.value = tab
  await loadRecords(tab)
}

// ── 注入策略 ──────────────────────────────────────────────────────────
const policyOpen = ref(false)
const policy = ref({
  allow_script_env: false,
  allow_external_host: true,
  max_inject_per_call: 3,
  readonly: true,
})
const prefs = ref({ allow_script_env: false })
const policyBusy = ref(false)

async function togglePolicy() {
  policyOpen.value = !policyOpen.value
  if (!policyOpen.value) return
  try {
    const [policyRes, prefsRes] = await Promise.all([
      axios.get('/api/portal/secrets/policy'),
      axios.get('/api/portal/secrets/prefs'),
    ])
    policy.value = policyRes.data?.data || policy.value
    prefs.value = prefsRes.data?.data || prefs.value
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载策略失败', 'err')
  }
}

async function savePrefs(value: boolean) {
  policyBusy.value = true
  try {
    await axios.patch('/api/portal/secrets/prefs', { allow_script_env: value })
    prefs.value.allow_script_env = value
    showToast(value ? '已允许脚本读取我的凭据' : '已禁止脚本读取我的凭据')
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '保存失败', 'err')
  } finally {
    policyBusy.value = false
  }
}

const sceneLabelOf = (key: string) => SCENES.find((scene) => scene.key === key)?.label || key

/** 表单预览里要显示的场景：过滤掉已废弃的那些。 */
const visibleFormScenarios = computed(() =>
  form.value.allowed_scenarios.filter((key) => !DEPRECATED_SCENES.has(key)),
)

function scenarioLabels(item: SecretItem): string[] {
  return item.allowed_scenarios
    .filter((key) => !DEPRECATED_SCENES.has(key))
    .map((key) => {
      const scene = SCENES.find((s) => s.key === key)
      if (!scene) return key
      return scene.wired ? scene.label : `${scene.label}（未接入）`
    })
}

function statusTone(item: SecretItem): string {
  if (isDisabled(item)) return 'bg-gray-100 text-gray-500'
  if (isExpired(item)) return 'bg-rose-50 text-rose-600'
  if ((item.binding_count || 0) === 0) return 'bg-slate-100 text-slate-500'
  return 'bg-emerald-50 text-emerald-700'
}

function statusText(item: SecretItem): string {
  if (isDisabled(item)) return '已停用'
  if (isExpired(item)) return '已过期'
  if ((item.binding_count || 0) === 0) return '未绑定'
  return '正常'
}

/** 「今天 10:32」「昨天 22:03」「11-06 08:00」——列表里只需要相对粗粒度 */

function categoryOf(item: SecretItem): string {
  return (item.category || '').trim() || '未分类'
}

function selectSecret(item: SecretItem) {
  selectedId.value = item.id
  void loadBindingsOf(item)
}

// ── 停用 / 启用 ──────────────────────────────────────────────────────
async function toggleStatus(item: SecretItem) {
  const next = isDisabled(item) ? 'active' : 'disabled'
  try {
    await axios.post(`/api/portal/secrets/${item.id}/status`, { status: next })
    showToast(next === 'disabled' ? '已停用，所有绑定调用会立即失败' : '已启用')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '操作失败', 'err')
  }
}

// ── 从这条凭据上解绑 ─────────────────────────────────────────────────
async function unbindFrom(row: any) {
  try {
    await axios.delete('/api/portal/secrets/bindings', {
      data: {
        consumer_type: row.consumer_type,
        consumer_id: row.consumer_id,
        slot: row.slot,
      },
    })
    showToast('已解除绑定')
    await Promise.all([load(), loadBindingsOf(selected.value)])
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '解除失败', 'err')
  }
}

// ── 选中项的绑定列表（右侧详情内嵌，不再单开弹窗）──────────────────
const selectedBindings = ref<any[]>([])
const selectedBindingsBusy = ref(false)

async function loadBindingsOf(item: SecretItem | null) {
  if (!item) {
    selectedBindings.value = []
    return
  }
  selectedBindingsBusy.value = true
  try {
    const res = await axios.get('/api/portal/secrets/bindings', {
      params: { secret_id: item.id },
    })
    selectedBindings.value = res.data?.data || []
  } catch {
    selectedBindings.value = []
  } finally {
    selectedBindingsBusy.value = false
  }
}

// 选中项跟着列表走：切 scope / 改筛选之后，右侧要显示当前列表里那一条
watch(selected, (item) => {
  if (item && item.id !== selectedId.value) selectedId.value = item.id
  void loadBindingsOf(item)
})

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <!-- 顶部：scope 切换 + 搜索 + 新建 -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="inline-flex rounded-xl bg-gray-100 p-1">
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-sm font-medium transition-colors"
          :class="scope === 'mine' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="scope = 'mine'"
        >
          我的凭据 <span class="ml-1 text-xs text-gray-400">{{ counts.mine }}</span>
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-sm font-medium transition-colors"
          :class="scope === 'shared' ? 'bg-white text-emerald-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="scope = 'shared'"
        >
          系统共享 <span class="ml-1 text-xs text-gray-400">{{ counts.shared }}</span>
        </button>
      </div>

      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-gray-300 text-sm font-semibold text-gray-500 transition-colors hover:border-blue-400 hover:text-blue-600"
          title="这些凭据会被用在哪里"
          aria-label="查看凭据使用范围"
          @click="usageHelpOpen = true"
        >?</button>
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索名称或标识"
          class="w-44 rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none sm:w-56"
        />
        <button
          v-if="scope === 'mine'"
          type="button"
          class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          @click="openCreate"
        >
          新建凭据
        </button>
      </div>
    </div>

    <!-- 安全提示 -->
    <div
      v-if="showSecurityHint"
      class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-800"
    >
      <p class="min-w-0 flex-1">
        <strong>凭据建完即不可读。</strong>
        「凭证」类型的值加密保存后，任何人（包括管理员）都无法再查看明文——只能轮换或停用；
        「变量」类型可以随时查看（每次查看都留记录）。想让某个 MCP / 工具用上，
        还需要在它那一侧<strong>显式绑定</strong>；建完不等于能用。
      </p>
      <button
        type="button"
        class="shrink-0 rounded p-0.5 text-amber-500 hover:bg-amber-100 hover:text-amber-800"
        title="关闭提示（不再显示）"
        aria-label="关闭安全提示"
        @click="dismissSecurityHint"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- 有待绑定的凭据：建完不绑等于没建，这条比任何说明都值得先说 -->
    <div
      v-if="unboundCount > 0 && !loading && showUnboundHint"
      class="flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-800"
    >
      <span class="font-semibold">{{ unboundCount }} 条凭据还没绑定到任何地方。</span>
      <span>凭据建完不等于能用——要去 API 工具的请求头或参数、MCP 服务的认证区或工具参数，或脚本的环境变量里显式绑上它。</span>
      <button type="button" class="ml-auto shrink-0 font-medium text-amber-900 underline" @click="filterKind = 'unbound'">
        只看这些 →
      </button>
      <button
        type="button"
        class="shrink-0 rounded p-0.5 text-amber-500 hover:bg-amber-100 hover:text-amber-800"
        title="关闭提示（不再显示）"
        aria-label="关闭未绑定提示"
        @click="dismissUnboundHint"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <div v-if="loading" class="py-12 text-center text-sm text-gray-400">加载中…</div>

    <!-- 只有「这个 scope 下一条都没有」才算整体空态。
         筛选后为空不是这种情况：左栏必须还在，否则用户连返回的入口都没有。 -->
    <div
      v-else-if="scopedItems.length === 0"
      class="rounded-xl border border-dashed border-gray-300 py-14 text-center"
    >
      <p class="text-sm text-gray-500">
        {{ scope === 'mine' ? '还没有凭据' : '没有可用的系统共享凭据' }}
      </p>
      <p class="mt-1 text-xs text-gray-400">
        {{ scope === 'mine' ? '点右上角「新建凭据」开始' : '系统级凭据由管理员创建并授权后才会出现在这里' }}
      </p>
    </div>

    <!-- 三栏：左筛选 / 中列表 / 右详情。
         右栏 v-if="selected"，没选中时它不渲染；栅格列必须跟着收成两栏，
         否则列表右边会剩下一整块 330px 的空白。 -->
    <div
      v-else
      class="grid grid-cols-1 gap-3"
      :class="selected
        ? 'lg:grid-cols-[176px_minmax(0,1fr)_330px]'
        : 'lg:grid-cols-[176px_minmax(0,1fr)]'"
    >
      <!-- 左：筛选 -->
      <aside class="rounded-xl border border-gray-200 p-2">
        <p class="px-2 py-1 text-[11px] font-semibold text-gray-400">全部</p>
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
          :class="filterKind === 'all' ? 'bg-blue-50 font-medium text-blue-700' : 'text-gray-600 hover:bg-gray-50'"
          @click="filterKind = 'all'"
        >
          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" />
          <span class="flex-1">所有凭据</span>
          <span class="text-[11px] text-gray-400">{{ scopedItems.length }}</span>
        </button>
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
          :class="filterKind === 'unbound' ? 'bg-amber-50 font-medium text-amber-700' : 'text-gray-600 hover:bg-gray-50'"
          @click="filterKind = 'unbound'"
        >
          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
          <span class="flex-1">未绑定</span>
          <span class="text-[11px] text-gray-400">{{ unboundCount }}</span>
        </button>
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
          :class="filterKind === 'expired' ? 'bg-rose-50 font-medium text-rose-700' : 'text-gray-600 hover:bg-gray-50'"
          @click="filterKind = 'expired'"
        >
          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
          <span class="flex-1">已过期</span>
          <span class="text-[11px] text-gray-400">{{ expiredCount }}</span>
        </button>

        <template v-if="categoryGroups.length">
          <p class="mt-3 px-2 py-1 text-[11px] font-semibold text-gray-400">我的分类</p>
          <button
            v-for="group in categoryGroups"
            :key="group.name"
            type="button"
            class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors"
            :class="filterKind === 'category' && activeCategory === group.name
              ? 'bg-violet-50 font-medium text-violet-700'
              : 'text-gray-600 hover:bg-gray-50'"
            @click="activeCategory = group.name; filterKind = 'category'"
          >
            <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400" />
            <span class="flex-1 truncate">{{ group.name }}</span>
            <span class="text-[11px] text-gray-400">{{ group.count }}</span>
          </button>
        </template>

        <p class="mt-3 px-2 py-1 text-[11px] font-semibold text-gray-400">快捷筛选</p>
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-gray-600 transition-colors hover:bg-gray-50"
          :class="filterKind === 'unused90' ? 'bg-gray-100 font-medium text-gray-800' : ''"
          @click="filterKind = 'unused90'"
        >
          <span class="flex-1">90 天未使用</span>
          <span class="text-[11px] text-gray-400">{{ unused90Count }}</span>
        </button>
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-gray-600 transition-colors hover:bg-gray-50"
          :class="filterKind === 'multibound' ? 'bg-gray-100 font-medium text-gray-800' : ''"
          @click="filterKind = 'multibound'"
        >
          <span class="flex-1">被多处绑定</span>
          <span class="text-[11px] text-gray-400">{{ multiboundCount }}</span>
        </button>
      </aside>

      <!-- 中：列表 -->
      <div class="min-w-0 overflow-hidden rounded-xl border border-gray-200">
        <div class="grid grid-cols-[minmax(0,1fr)_84px_56px_84px] gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2 text-[11px] font-medium text-gray-500">
          <span>名称 / 标识</span>
          <span>状态</span>
          <span class="text-center">绑定</span>
          <span class="text-right">最近使用</span>
        </div>

        <div v-if="filtered.length === 0" class="px-3 py-12 text-center">
          <p class="text-xs text-gray-500">没有符合当前筛选的凭据</p>
          <button
            type="button"
            class="mt-2 text-xs text-blue-600 underline hover:text-blue-700"
            @click="resetFilters"
          >
            清除筛选
          </button>
        </div>

        <template v-else>
          <button
            v-for="item in filtered"
            :key="item.id"
            type="button"
            class="grid w-full grid-cols-[minmax(0,1fr)_84px_56px_84px] items-center gap-2 border-b border-gray-50 px-3 py-2.5 text-left transition-colors last:border-b-0"
            :class="selected?.id === item.id ? 'bg-blue-50/60' : 'hover:bg-gray-50'"
            @click="selectSecret(item)"
          >
          <div class="flex min-w-0 items-center gap-2">
            <span
              class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold"
              :class="item.secret_type === 'variable' ? 'bg-sky-100 text-sky-700' : 'bg-violet-100 text-violet-700'"
            >{{ item.secret_type === 'variable' ? '变' : '凭' }}</span>
            <div class="min-w-0">
              <p class="truncate text-xs font-medium text-gray-800">
                {{ item.name }}
                <span
                  v-if="item.owner_scope === 'system'"
                  class="ml-1 rounded bg-emerald-50 px-1 py-0.5 text-[10px] font-normal text-emerald-700"
                >系统</span>
              </p>
              <p class="truncate text-[11px] text-gray-400">
                <code class="font-mono">{{ item.secret_key }}</code> · {{ categoryOf(item) }}
              </p>
            </div>
          </div>
          <span class="rounded-md px-1.5 py-0.5 text-center text-[11px] font-medium" :class="statusTone(item)">
            {{ statusText(item) }}
          </span>
          <span class="text-center text-xs" :class="(item.binding_count || 0) ? 'text-gray-700' : 'text-gray-300'">
            {{ item.binding_count || 0 }}
          </span>
          <span class="text-right text-[11px] text-gray-400">{{ shortTime(item.last_used_at) }}</span>
          </button>
        </template>
      </div>

      <!-- 右：详情 -->
      <aside v-if="selected" class="min-w-0 rounded-xl border border-gray-200">
        <div class="border-b border-gray-100 p-3">
          <div class="flex items-start gap-2">
            <span
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-xs font-semibold"
              :class="selected.secret_type === 'variable' ? 'bg-sky-100 text-sky-700' : 'bg-violet-100 text-violet-700'"
            >{{ selected.secret_type === 'variable' ? '变' : '凭' }}</span>
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-gray-900">{{ selected.name }}</p>
              <p class="truncate font-mono text-[11px] text-gray-400">{{ selected.secret_key }}</p>
            </div>
          </div>
          <div class="mt-2 flex flex-wrap gap-1">
            <span class="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
              {{ selected.secret_type === 'variable' ? '变量' : '凭证' }}
            </span>
            <span class="rounded px-1.5 py-0.5 text-[10px] font-medium" :class="statusTone(selected)">
              {{ statusText(selected) }}
            </span>
            <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">{{ categoryOf(selected) }}</span>
          </div>
        </div>

        <div class="space-y-3 p-3">
          <!-- 值 -->
          <div>
            <p class="text-[11px] font-medium text-gray-500">
              值（{{ selected.secret_type === 'variable' ? '可读' : '不可读' }}）
            </p>
            <div class="mt-1 flex items-center gap-2 rounded-lg bg-gray-900 px-2 py-1.5">
              <svg class="h-3.5 w-3.5 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <rect x="3" y="11" width="18" height="10" rx="2" stroke-width="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" stroke-width="2" />
              </svg>
              <code class="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-300">
                {{ selected.secret_type === 'variable' ? selected.value_masked : '••••••••••••••••' }}
              </code>
              <button
                v-if="selected.secret_type === 'variable'"
                type="button"
                class="shrink-0 rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-gray-200 hover:bg-white/20"
                @click="openReveal(selected)"
              >查看</button>
            </div>
            <p class="mt-1 text-[10px] leading-relaxed text-gray-400">
              <template v-if="selected.secret_type === 'variable'">
                变量类（租户号、接口地址）可读可复制。
              </template>
              <template v-else>
                凭证创建后<b>任何界面都不再显示明文</b>，包括你自己。要换新值用「轮换」。
              </template>
            </p>
          </div>

          <!-- 场景 -->
          <div>
            <p class="text-[11px] font-medium text-gray-500">允许注入的场景</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="label in scenarioLabels(selected)"
                :key="label"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600"
              >{{ label }}</span>
              <span v-if="!selected.allowed_scenarios.length" class="text-[10px] text-amber-600">
                未启用任何场景，不会被注入
              </span>
            </div>
          </div>

          <!-- 域名 -->
          <div>
            <p class="text-[11px] font-medium text-gray-500">允许发往的域名</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <code
                v-for="host in selected.allowed_hosts"
                :key="host"
                class="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-600"
              >{{ host }}</code>
              <span v-if="!selected.allowed_hosts.length" class="text-[10px] text-gray-400">
                仅允许平台内部目标
              </span>
            </div>
          </div>

          <!-- 绑定到哪 -->
          <div>
            <p class="text-[11px] font-medium text-gray-500">
              已绑定（{{ selectedBindingsBusy ? '…' : selectedBindings.length }}）
            </p>
            <p v-if="selectedBindingsBusy" class="mt-1 text-[10px] text-gray-400">加载中…</p>
            <p
              v-else-if="!selectedBindings.length"
              class="mt-1 rounded-lg border border-dashed border-gray-200 px-2 py-3 text-center text-[10px] text-gray-400"
            >
              还没绑定到任何工具，所以不会被注入
            </p>
            <div v-else class="mt-1 space-y-1">
              <div
                v-for="row in selectedBindings"
                :key="row.id"
                class="flex items-center gap-1.5 rounded-lg bg-gray-50 px-2 py-1.5"
              >
                <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-white text-[10px] font-semibold text-gray-600">
                  {{ (CONSUMER_LABEL[row.consumer_type] || '?').slice(0, 1) }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-[10px] font-medium text-gray-700">
                    {{ CONSUMER_LABEL[row.consumer_type] || row.consumer_type }}
                  </p>
                  <p class="truncate font-mono text-[10px] text-gray-400">{{ row.slot }}</p>
                </div>
                <span
                  class="shrink-0 rounded px-1 py-0.5 text-[9px]"
                  :class="row.bound_by_me ? 'bg-emerald-100 text-emerald-700' : 'bg-sky-100 text-sky-700'"
                >{{ row.bound_by_me ? '我绑的' : '全局' }}</span>
                <button
                  v-if="row.bound_by_me"
                  type="button"
                  class="shrink-0 text-[9px] text-gray-400 hover:text-rose-600"
                  :title="`从 ${CONSUMER_LABEL[row.consumer_type] || row.consumer_type} 解除`"
                  @click.stop="unbindFrom(row)"
                >移除</button>
              </div>
            </div>
          </div>

          <!-- 使用概览 -->
          <div>
            <p class="text-[11px] font-medium text-gray-500">使用概览</p>
            <p class="mt-1 text-[10px] leading-relaxed text-gray-600">
              近 7 天调用 <b>{{ selected.usage_7d || 0 }}</b> 次<template v-if="selected.failed_7d">
                · 失败 <b class="text-rose-600">{{ selected.failed_7d }}</b> 次</template><br />
              最近一次：{{ shortTime(selected.last_used_at) }}<br />
              创建于 {{ shortTime(selected.created_at) }}<template v-if="selected.expires_at"> · 有效期至 {{ shortTime(selected.expires_at) }}</template>
            </p>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-1.5 border-t border-gray-100 p-3">
          <button
            v-if="selected.owner_scope === 'user'"
            type="button"
            class="rounded-lg border border-gray-300 px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            title="改名称、场景、域名白名单（值只能轮换）"
            @click="openEdit"
          >编辑</button>
          <button
            v-if="selected.owner_scope === 'user'"
            type="button"
            class="rounded-lg border border-gray-300 px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            @click="openRotate(selected)"
          >轮换</button>
          <button
            v-if="selected.owner_scope === 'user'"
            type="button"
            class="rounded-lg border border-gray-300 px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            @click="toggleStatus(selected)"
          >{{ isDisabled(selected) ? '启用' : '停用' }}</button>
          <button
            type="button"
            class="rounded-lg border border-gray-300 px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            @click="openUsage"
          >使用记录</button>
          <button
            v-if="selected.owner_scope === 'user'"
            type="button"
            class="ml-auto rounded-lg border border-rose-200 px-2 py-1 text-[11px] text-rose-600 hover:bg-rose-50"
            @click="deleteTarget = selected"
          >删除</button>
          <span v-else class="text-[10px] text-gray-400">系统级凭据由管理员维护</span>
        </div>
      </aside>
    </div>

    <!-- 用在哪里：这层信息原来只写在代码里，界面上完全没有，每次都要口头解释 -->
    <Teleport to="body">
      <div
        v-if="usageHelpOpen"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="usageHelpOpen = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-gray-100 px-5 py-3">
            <div>
              <h3 class="text-base font-semibold text-gray-900">这些凭据会被用在哪里</h3>
              <p class="mt-0.5 text-xs text-gray-500">只有下面五处会真的读到凭据的值；其余位置即使勾了也不会生效。</p>
            </div>
            <button
              type="button"
              class="rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              @click="usageHelpOpen = false"
            >✕</button>
          </div>

          <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
            <div>
              <p class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">会注入</p>
              <div class="space-y-2">
                <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-800">MCP 服务 · 请求头</span>
                    <code class="text-[11px] text-gray-500">header:</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">绑定盖过固定 Token。绑定失效时直接报错，不会退回固定 Token——那等于用别人的账号，还查不出来。</p>
                </div>
                <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-800">MCP 服务 · 工具参数</span>
                    <code class="text-[11px] text-gray-500">arg:</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">
                    工具端把某个入参（例如 <code>api_key</code>）标成「来自凭据」后，该参数从模型可见的定义里消失，调用时由平台补进 <code>tools/call</code>。
                    这类参数没有兜底值：没绑定或绑定的凭据不可用，这次调用直接失败并说明缺哪个。
                  </p>
                </div>
                <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-800">API 工具 · 请求头</span>
                    <code class="text-[11px] text-gray-500">header</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">只补空位：工具里手填了值的头不会被覆盖。</p>
                </div>
                <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-800">API 工具 · 参数位</span>
                    <code class="text-[11px] text-gray-500">path: / query: / body:</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">
                    只有参数显式声明 <code>source: credential</code> 才走凭据，这些参数对模型不可见。
                    具体落到 path 还是 query / body，由工具的 URL 和方法决定（URL 里有 <code>{"{参数名}"}</code> 就是 path，否则 GET 走 query、其余方法走 body），改配置时会跟着迁移。
                  </p>
                </div>
                <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-800">脚本工具 · 环境变量</span>
                    <code class="text-[11px] text-gray-500">env:</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">平台和用户两层默认都关，两边都打开才能用。</p>
                </div>
              </div>
            </div>

            <div>
              <p class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">不会注入</p>
              <div class="space-y-2">
                <div class="rounded-lg border border-gray-100 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-700">定时任务</span>
                    <code class="text-[11px] text-gray-400">沿用同一套判定</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">不是单独的注入点：定时任务跑起来后，用的就是它里面那个工具或 MCP 的通道，判定和你在对话里手动调用时完全一样。</p>
                </div>
                <div class="rounded-lg border border-gray-100 px-3 py-2">
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="font-medium text-gray-700">智能体</span>
                    <code class="text-[11px] text-gray-400">暂未实现</code>
                  </div>
                  <p class="mt-1 text-xs leading-relaxed text-gray-500">勾了目前没有任何效果。</p>
                </div>
              </div>
            </div>

            <div>
              <p class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">三道闸门都要过</p>
              <ol class="space-y-1 text-xs leading-relaxed text-gray-600">
                <li><b class="text-gray-800">① 平台策略</b>：是否允许脚本读凭据、是否允许打外部域名、单次最多注入几条。</li>
                <li><b class="text-gray-800">② 你的偏好</b>：脚本场景需要你在这里单独打开。</li>
                <li><b class="text-gray-800">③ 这条凭据自己</b>：允许的场景、允许的域名；系统共享的凭据还要被授权给你。</li>
              </ol>
            </div>

            <p class="rounded-lg bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-800">
              值是加密存的，只在注入那一刻解密，不落日志、不进对话历史。你自己在页面上点「查看」也会留一条读取记录。
            </p>
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
              <code class="rounded bg-gray-100 px-1 py-0.5">{{ selected?.secret_key }}</code>
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
                  v-for="scene in SCENES"
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
                  placeholder="左栏筛选用"
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
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-gray-100 px-5 py-3">
            <div>
              <h3 class="text-base font-semibold text-gray-900">
                {{ recordTab === 'usage' ? '使用记录' : recordTab === 'stats' ? '使用统计' : '变更记录' }}
              </h3>
              <p class="mt-0.5 text-xs text-gray-500">
                {{ recordTab === 'usage'
                  ? '谁在什么时候、用在哪条链路上。只记结果不记值——值从不落日志。'
                  : recordTab === 'stats'
                    ? '近 7 天的调用量、成功与被拒绝的次数。被拒绝的也算进来——「配了但用不了」更值得看。'
                    : '谁在什么时候对哪条凭据做了什么。凭据删除后记录仍保留。' }}
              </p>
            </div>
            <button
              type="button"
              class="rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              @click="usageOpen = false"
            >✕</button>
          </div>

          <div class="flex items-center gap-1 border-b border-gray-100 px-5">
            <button
              v-for="tab in (['usage', 'stats', 'audit'] as const)"
              :key="tab"
              type="button"
              class="-mb-px border-b-2 px-2 py-2 text-xs font-medium transition"
              :class="recordTab === tab
                ? 'border-emerald-500 text-emerald-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'"
              @click="switchRecordTab(tab)"
            >{{ tab === 'usage' ? '使用记录' : tab === 'stats' ? '统计' : '变更记录' }}</button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-3">
            <!-- 统计面板自己管加载态，不受明细的 usageLoading 影响 -->
            <!-- 个人端没有「全部凭据」这个视角：统计跟着左栏选中的那条走，
                 没选中时就是我这全部凭据的合计 -->
            <SecretUsageStats
              v-if="recordTab === 'stats'"
              :secret-id="selectedId || null"
            />

            <p v-else-if="usageLoading" class="py-8 text-center text-xs text-gray-400">加载中…</p>

            <template v-else-if="recordTab === 'audit'">
              <p v-if="!auditRows.length" class="py-8 text-center text-xs text-gray-400">
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
                  <span class="ml-auto text-gray-400">{{ shortTime(row.created_at) }}</span>
                </div>
              </div>
            </template>

            <p v-else-if="!usageRows.length" class="py-8 text-center text-xs text-gray-400">
              还没有使用记录
            </p>
            <div v-else class="space-y-1.5">
              <div
                v-for="row in usageRows"
                :key="row.id"
                class="flex flex-wrap items-center gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-600"
              >
                <code class="text-gray-800">{{ row.secret_key || '已删除' }}</code>
                <span class="rounded bg-white px-1.5 py-0.5">{{ row.scene }}</span>
                <span
                  class="rounded px-1.5 py-0.5 font-medium"
                  :class="row.result === 'success' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'"
                >
                  {{ RESULT_LABEL[row.result] || row.result }}
                </span>
                <span v-if="row.target_host" class="text-gray-400">{{ row.target_host }}</span>
                <!-- 个人中心虽然都是自己的记录，但仍显示身份：多账号 / 被授权他人凭据时，
                     「这条到底是哪个账号在调用」是排查的第一步 -->
                <span
                  v-if="row.user_real_name || row.user_name || row.user_id"
                  class="text-gray-400"
                  :title="[row.user_email, row.user_id ? `ID: ${row.user_id}` : ''].filter(Boolean).join('\n')"
                >{{ row.user_real_name || row.user_name || row.user_id }}</span>
                <span class="ml-auto text-gray-400">{{ shortTime(row.created_at) }}</span>
              </div>
            </div>
          </div>

          <div class="flex items-center justify-end border-t border-gray-100 px-5 py-3">
            <button
              type="button"
              class="rounded-lg btn-ghost px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              @click="usageOpen = false"
            >关闭</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 注入策略 -->
    <div class="rounded-xl border border-gray-200">
      <button
        type="button"
        class="flex w-full items-center justify-between px-4 py-3 text-left"
        @click="togglePolicy"
      >
        <span class="text-sm font-medium text-gray-800">注入策略</span>
        <span class="text-xs text-gray-400">{{ policyOpen ? '收起' : '展开' }}</span>
      </button>

      <div v-if="policyOpen" class="space-y-3 border-t border-gray-100 px-4 py-3 text-xs text-gray-600">
        <div class="flex items-start justify-between gap-4 rounded-lg bg-gray-50 p-3">
          <div>
            <p class="font-medium text-gray-800">平台允许脚本从环境变量读取凭据</p>
            <p class="mt-0.5 text-[11px] text-gray-500">
              脚本能 <code>print(os.environ)</code>，值进了子进程就没有任何手段能拦住它回显。默认关，且关闭时是整类拒绝。
            </p>
          </div>
          <span
            class="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium"
            :class="policy.allow_script_env ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
          >
            {{ policy.allow_script_env ? '已开启' : '已关闭' }}
          </span>
        </div>

        <div class="flex items-start justify-between gap-4 rounded-lg bg-gray-50 p-3">
          <div>
            <p class="font-medium text-gray-800">平台允许凭据发往外部域名</p>
            <p class="mt-0.5 text-[11px] text-gray-500">
              权威开关仍是每条凭据自己的域名白名单；这一项是部署级兜底闸门。
            </p>
          </div>
          <span
            class="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium"
            :class="policy.allow_external_host ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'"
          >
            {{ policy.allow_external_host ? '已开启' : '已关闭' }}
          </span>
        </div>

        <div class="flex items-start justify-between gap-4 rounded-lg bg-blue-50 p-3">
          <div>
            <p class="font-medium text-gray-800">我的偏好：允许脚本读取我的凭据</p>
            <p class="mt-0.5 text-[11px] text-gray-500">
              与上面的平台策略取交集——两层都开才真正生效。
            </p>
          </div>
          <button
            type="button"
            :disabled="policyBusy"
            class="shrink-0 rounded-md px-2.5 py-1 text-[11px] font-medium disabled:opacity-60"
            :class="prefs.allow_script_env ? 'bg-gray-200 text-gray-700' : 'bg-blue-600 text-white'"
            @click="savePrefs(!prefs.allow_script_env)"
          >
            {{ prefs.allow_script_env ? '改为禁止' : '允许' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 创建弹窗：三步向导 -->
    <Teleport to="body">
      <div
        v-if="showCreate"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
        @click.self="showCreate = false"
      >
        <div class="flex max-h-[calc(100vh-120px)] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div class="border-b border-gray-100 px-5 py-3">
            <h3 class="flex items-center gap-1.5 text-base font-semibold text-gray-900">
              <span class="text-blue-600">+</span> 新建凭据
            </h3>
            <p class="mt-0.5 text-xs text-gray-500">三步：说清楚是什么 → 划清楚能去哪 → 确认不可逆的部分</p>
          </div>

          <!-- 步骤条 -->
          <div class="flex items-center gap-0 bg-gray-50 px-5 py-2.5">
            <template v-for="(label, index) in ['基本信息', '注入范围', '确认']" :key="label">
              <div
                class="flex items-center gap-1.5 text-[11.5px] font-semibold"
                :class="createStep === index + 1 ? 'text-blue-700' : (createStep > index + 1 ? 'text-emerald-700' : 'text-gray-400')"
              >
                <span
                  class="flex h-[19px] w-[19px] items-center justify-center rounded-full text-[10px]"
                  :class="createStep === index + 1 ? 'bg-blue-600 text-white' : (createStep > index + 1 ? 'bg-emerald-600 text-white' : 'bg-gray-200 text-gray-500')"
                >{{ createStep > index + 1 ? '✓' : index + 1 }}</span>
                {{ label }}
              </div>
              <span
                v-if="index < 2"
                class="mx-2.5 h-0.5 w-6 rounded bg-gray-300"
                :class="createStep > index + 1 ? 'bg-emerald-500' : ''"
              />
            </template>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <!-- 步骤 1：基本信息 -->
            <div v-if="createStep === 1" class="space-y-3">
              <label class="block">
                <span class="text-xs font-medium text-gray-700">凭据标识 <span class="text-rose-500">*</span></span>
                <input
                  v-model="form.secret_key"
                  placeholder="jira.prod.token"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
                />
                <span class="mt-1 block text-[11px] text-gray-400">
                  小写字母、数字、点、下划线、连字符；会被拼进 <code>${secret:标识}</code>
                </span>
              </label>

              <label class="block">
                <span class="text-xs font-medium text-gray-700">名称</span>
                <input
                  v-model="form.name"
                  placeholder="留空则用标识"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
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
                    <span class="text-[11px] text-gray-500">租户号 / 地址，可复制</span>
                  </button>
                </div>
              </div>

              <label class="block">
                <span class="text-xs font-medium text-gray-700">分类</span>
                <input
                  v-model="form.category"
                  list="secret-category-options"
                  placeholder="第三方 API / 数据库 / 平台内部……"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
                <datalist id="secret-category-options">
                  <option v-for="group in categoryGroups" :key="group.name" :value="group.name" />
                </datalist>
                <span class="mt-1 block text-[11px] text-gray-500">左侧筛选栏就是按这个分组的，留空归到「未分类」。</span>
              </label>

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
                  class="mt-1 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm leading-relaxed focus:border-blue-500 focus:outline-none"
                  :class="{ 'masked-value': !revealValue }"
                ></textarea>
                <span class="mt-1 block text-[11px] text-amber-600">
                  提交后不可再查看，只能轮换或停用。多行内容会原样保存，不会被 trim。
                </span>
              </label>

              <label class="block">
                <span class="text-xs font-medium text-gray-700">备注</span>
                <input
                  v-model="form.remark"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
              </label>
            </div>

            <!-- 步骤 2：注入范围 -->
            <div v-else-if="createStep === 2" class="space-y-3">
              <div>
                <span class="text-xs font-medium text-gray-700">允许注入到哪些场景 <span class="text-rose-500">*</span></span>
                <div class="mt-1 space-y-1.5">
                  <button
                    v-for="scene in SCENES"
                    :key="scene.key"
                    type="button"
                    :disabled="!scene.wired"
                    class="flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors"
                    :class="scene.wired
                      ? (form.allowed_scenarios.includes(scene.key) ? 'border-blue-400 bg-blue-50' : 'border-gray-200 hover:border-gray-300')
                      : 'cursor-not-allowed border-dashed border-gray-200 bg-gray-50 opacity-70'"
                    @click="scene.wired && toggleScene(scene.key)"
                  >
                    <span
                      class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] text-white"
                      :class="form.allowed_scenarios.includes(scene.key) ? 'border-blue-600 bg-blue-600' : 'border-gray-300 bg-white'"
                    >{{ form.allowed_scenarios.includes(scene.key) ? '✓' : '' }}</span>
                    <span class="min-w-0">
                      <span class="block text-xs font-medium" :class="scene.wired ? 'text-gray-900' : 'text-gray-400'">
                        {{ scene.label }}
                        <span
                          v-if="!scene.wired"
                          class="ml-1 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-normal text-amber-700"
                        >暂未接入</span>
                      </span>
                      <span class="mt-0.5 block text-[11px] leading-snug" :class="scene.wired ? 'text-gray-500' : 'text-amber-600'">
                        {{ scene.desc }}
                      </span>
                    </span>
                  </button>
                </div>
              </div>

              <label class="block">
                <span class="text-xs font-medium text-gray-700">允许发往的域名 <span class="text-rose-500">*</span></span>
                <input
                  v-model="form.allowed_hosts"
                  placeholder="api.example.com, *.atlassian.net"
                  class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
                />
                <span class="mt-1 block text-[11px] text-gray-400">
                  逗号分隔，支持 <code>*.</code> 通配子域。留空 = 只允许平台内部目标。这条是防「凭据被拼进任意 URL 带走」的闸门。
                </span>
              </label>

              <div class="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-[11px] leading-relaxed text-blue-800">
                <b>不勾任何场景</b> = 这条凭据建完只是个仓库条目，任何工具都取不到。
                绑定是白名单，不是黑名单。
              </div>
            </div>

            <!-- 步骤 3：确认 -->
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
                  <dd class="text-gray-900">{{ form.secret_type === 'credential' ? '凭证（建完不可读）' : '变量（可复制）' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">分类</dt>
                  <dd class="text-gray-900">{{ form.category || '未分类' }}</dd>
                </div>
                <div class="flex items-start gap-3 px-3 py-2">
                  <dt class="w-20 shrink-0 text-gray-500">场景</dt>
                  <dd class="flex flex-wrap gap-1">
                    <template v-if="visibleFormScenarios.length">
                      <span
                        v-for="key in visibleFormScenarios"
                        :key="key"
                        class="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-700"
                      >{{ sceneLabelOf(key) }}</span>
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
                <b>点了创建就回不去了</b>：值会被加密保存，你和管理员都再看不到明文，
                唯一的后续手段是轮换（旧值保留 7 天过渡）或停用。
              </div>
            </div>
          </div>

          <div class="flex items-center gap-2 border-t border-gray-100 px-5 py-3">
            <span class="text-[11px] text-gray-400">
              <template v-if="createStep === 1">下一步会让你确认「创建后不可查看明文」</template>
              <template v-else-if="createStep === 2">还剩一步：确认</template>
              <template v-else>确认后立即生效</template>
            </span>
            <div class="ml-auto flex items-center gap-2">
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
                @click="showCreate = false"
              >取消</button>
              <button
                v-if="createStep > 1"
                type="button"
                class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
                @click="prevCreateStep"
              >上一步</button>
              <button
                v-if="createStep < 3"
                type="button"
                class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
                @click="nextCreateStep"
              >下一步</button>
              <button
                v-else
                type="button"
                :disabled="createBusy"
                class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                @click="submitCreate"
              >{{ createBusy ? '创建中…' : '确认创建' }}</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 轮换弹窗 -->
    <Teleport to="body">
          <div
            v-if="rotateTarget"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="rotateTarget = null"
          >
            <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">轮换凭据值</h3>
              <p class="mt-1 text-xs text-gray-500">
                <code>{{ rotateTarget.secret_key }}</code> 的新值。旧值会保留 7 天，正在运行的任务不会中断。
              </p>
              <div class="mt-3 flex items-center justify-end">
                <button
                  type="button"
                  class="rounded px-1.5 py-0.5 text-[11px] text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                  @click="revealRotateValue = !revealRotateValue"
                >{{ revealRotateValue ? '隐藏' : '显示' }}</button>
              </div>
              <textarea
                v-model="rotateValue"
                :rows="3"
                spellcheck="false"
                autocomplete="off"
                class="mt-1 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm leading-relaxed focus:border-blue-500 focus:outline-none"
                :class="{ 'masked-value': !revealRotateValue }"
              ></textarea>
              <div class="mt-4 flex justify-end gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="rotateTarget = null">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="rotateBusy || !rotateValue.trim()"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                  @click="submitRotate"
                >
                  {{ rotateBusy ? '轮换中…' : '确认轮换' }}
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
              <p class="mt-1 text-xs text-gray-500">
                <code>{{ deleteTarget.secret_key }}</code> 会被删除。
                <template v-if="deleteTarget.binding_count">
                  它当前绑在 <b class="text-rose-600">{{ deleteTarget.binding_count }}</b> 个地方，这些调用会立刻开始失败。
                </template>
                使用记录会保留。
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

    <!-- 变量取值 -->
    <Teleport to="body">
          <div
            v-if="revealOpen && revealTarget"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="revealOpen = false"
          >
            <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">变量值</h3>
              <p class="mt-1 text-xs text-gray-500">
                <code>{{ revealTarget.secret_key }}</code> 是「变量」类型，值本身就是要填到别处去的，所以可以读。
              </p>
              <div class="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <p v-if="revealBusy" class="text-xs text-gray-400">读取中…</p>
                <code v-else class="block break-all font-mono text-sm text-gray-800">{{ revealedValue || '（空）' }}</code>
              </div>
              <p class="mt-2 text-[11px] text-amber-600">
                这次读取会记一条使用记录。不要把值贴到聊天里——对话内容会进审计。
              </p>
              <div class="mt-4 flex justify-end gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="revealOpen = false">
                  关闭
                </button>
                <button
                  type="button"
                  :disabled="revealBusy || !revealedValue"
                  class="rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-60"
                  @click="copyRevealed"
                >
                  复制
                </button>
              </div>
            </div>
          </div>
    </Teleport>

    <!-- Toast -->
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
