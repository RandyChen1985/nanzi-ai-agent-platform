<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { KeyIcon } from '@heroicons/vue/24/outline'
import axios from '../../utils/axios'
import PermissionVariableMenu from '../metadata/PermissionVariableMenu.vue'
import CredentialPicker from '../secrets/CredentialPicker.vue'
import SecretLocatorEditor, { ruleSummary, type LocatorRule } from '../secrets/SecretLocatorEditor.vue'
import { SYSTEM_PARAM_SOURCES, SYSTEM_PARAM_TYPES, type SystemParam } from '../../utils/toolSchema'
import type { UserVariable } from '../../utils/userVariables'

interface BoundSecret {
  secret_id: string
  secret_key: string
  /** 绑定还在，但凭据可能已被停用/过期 —— 界面要标出来 */
  secret_status?: string
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

const props = withDefaults(defineProps<{
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
const bindings = ref<Record<string, BoundSecret>>({})
const pending = ref<Record<string, BoundSecret>>({})
const pickingName = ref<string | null>(null)
const pickerSecretId = ref<string | null>(null)
const pickerItem = ref<{ id: string; secret_key: string } | null>(null)
const pickerBusy = ref(false)
const pickerError = ref('')

// ── 规则定位（第三种来源）────────────────────────────────────────────
// 与固定凭据分开存：两者是同槽位的两条独立绑定，共用一个 key 会互相覆盖。
/** 参数名 → 已保存的规则 */
const ruleDrafts = ref<Record<string, LocatorRule | null>>({})
/** 新建态（工具还没有 id）下暂存的规则，保存后补写 */
const pendingRules = ref<Record<string, LocatorRule>>({})
/** 正在编辑规则的参数名；非空时显示编辑器 */
const locatorName = ref<string | null>(null)

const bindingOf = (name: string): BoundSecret | null => {
  const key = String(name || '').trim()
  if (!key) return null
  return bindings.value[key] || pending.value[key] || null
}

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
        nextRules[name] = row.rule || null
        continue
      }
      next[name] = {
        secret_id: row.secret_id || '',
        secret_key: row.secret_key || '',
        secret_status: row.secret_status,
        strategy,
      }
    }
    bindings.value = next
    ruleDrafts.value = nextRules
  } catch {
    // 拉不到就按「未绑」显示，别挡住参数编辑
    bindings.value = {}
  }
}

watch(
  () => [props.consumerId, props.consumerType],
  () => { void loadBindings() },
  { immediate: true },
)

function openPicker(name: string) {
  const key = String(name || '').trim()
  if (!key) {
    pickerError.value = '先给这个参数起个名字，再选凭据'
    return
  }
  pickingName.value = key
  const existing = bindingOf(key)
  pickerSecretId.value = existing?.secret_id || null
  pickerItem.value = existing
    ? { id: existing.secret_id, secret_key: existing.secret_key }
    : null
  pickerError.value = ''
}

function onPicked(item: any) {
  pickerItem.value = item || null
}

function closePicker() {
  pickingName.value = null
  pickerSecretId.value = null
  pickerItem.value = null
  pickerError.value = ''
}

async function confirmPick() {
  const name = pickingName.value
  if (!name) return
  const picked = pickerSecretId.value ? pickerItem.value : null
  pickerBusy.value = true
  pickerError.value = ''
  try {
    // 还在新建态：工具没有 id，先记账，保存拿到 id 后由父组件调 flushPending
    if (!props.consumerId) {
      if (picked) {
        pending.value[name] = { secret_id: picked.id, secret_key: picked.secret_key }
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
          is_global: true,
        },
      })
      // 两个 map 都要清：解绑后只要任一还留着 key，界面就还显示「已绑定」
      delete bindings.value[name]
      delete pending.value[name]
    } else {
      await axios.post('/api/portal/secrets/bindings', {
        secret_id: picked.id,
        consumer_type: props.consumerType,
        consumer_id: props.consumerId,
        // 只给参数名：槽位（path/query/body）由后端按工具的 URL 和方法算
        name,
        // 系统管理里配的绑定给全平台用；不设全局的话只有管理员自己能用。
        is_global: true,
      })
      bindings.value[name] = { secret_id: picked.id, secret_key: picked.secret_key }
      // 已经落库了，待写记录没有意义；留着会在保存时又写一遍
      delete pending.value[name]
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
        is_global: true,
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
    try {
      await axios.post('/api/portal/secrets/bindings', {
        consumer_type: props.consumerType,
        consumer_id: consumerId,
        name,
        strategy: 'rule',
        rule,
        is_global: true,
      })
    } catch {
      ruleFailed.push(name)
    }
  }
  pendingRules.value = {}
  return [...failed, ...ruleFailed]
}

function openLocator(name: string) {
  const key = String(name || '').trim()
  if (!key) {
    pickerError.value = '先给这个参数起个名字，再配规则'
    return
  }
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
  locatorName.value = null
  if (!name) return
  // 还没有 id：先记账，保存工具后由 flushPending 补写
  if (!props.consumerId) {
    pendingRules.value[name] = rule
    ruleDrafts.value[name] = rule
    return
  }
  pickerBusy.value = true
  pickerError.value = ''
  try {
    await axios.post('/api/portal/secrets/bindings', {
      consumer_type: props.consumerType,
      consumer_id: props.consumerId,
      // 只给参数名：槽位（path/query/body）由后端按 URL 与方法推算
      name,
      strategy: 'rule',
      rule,
      is_global: true,
    })
    ruleDrafts.value[name] = rule
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
      <button
        type="button"
        class="rounded-md border border-indigo-300 bg-white px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
        @click="addParam"
      >+ 添加系统级参数</button>
    </div>

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
        <button
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
        <span
          v-if="fixedBindingOf(p.name)"
          class="max-w-[12rem] truncate font-mono text-[11px]"
          :class="bindingIssue(fixedBindingOf(p.name)) ? 'text-amber-700' : 'text-emerald-700'"
          :title="bindingIssue(fixedBindingOf(p.name))
            ? `已绑定 ${fixedBindingOf(p.name)?.secret_key}，但${bindingIssue(fixedBindingOf(p.name))}`
            : ''"
        >{{ fixedBindingOf(p.name)?.secret_key }}</span>
        <span
          v-if="bindingIssue(fixedBindingOf(p.name))"
          class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
        >{{ bindingIssue(fixedBindingOf(p.name)) }}</span>
        <span v-else class="text-[11px] text-red-600">未选，保存会被拦住</span>
        <span
          v-if="!consumerId && pending[p.name.trim()]"
          class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
        >保存后生效</span>
      </div>

            <!-- 按规则匹配：不选具体凭据，只配「怎么找」 -->
      <div v-else-if="p.source === 'rule'" class="flex flex-wrap items-center gap-1.5">
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

        <div class="mt-3">
          <CredentialPicker v-model="pickerSecretId" scene="api_tool" @picked="onPicked" />
        </div>

        <p v-if="pickerError" class="mt-2 text-[11px] text-red-600">{{ pickerError }}</p>
        <p v-else-if="bindingOf(pickingName)" class="mt-2 text-[11px] text-emerald-600">
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
  <SecretLocatorEditor
    :open="Boolean(locatorName)"
    :rule="locatorName ? (ruleDrafts[locatorName] || null) : null"
    :slot="locatorName || ''"
    :arg-options="locatorName ? locatorArgOptions(locatorName) : []"
    :title="locatorName ? `凭据匹配规则 · ${locatorName}` : '凭据匹配规则'"
    @update:open="(value: boolean) => { if (!value) locatorName = null }"
    @save="applyRule"
  />
</template>
