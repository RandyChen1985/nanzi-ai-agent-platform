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

type Binding = {
  secret_id: string
  secret_key: string
  secret_name: string
  /** 绑定还在，但凭据可能已被停用/过期 */
  secret_status?: string
}

/** 绑了却用不了，比没绑更该提示——否则用户只会看到调用失败。 */
const bindingIssue = (binding?: Binding | null): string => {
  if (!binding || !binding.secret_status || binding.secret_status === 'active') return ''
  if (binding.secret_status === 'disabled') return '已停用'
  if (binding.secret_status === 'expired') return '已过期'
  return '不可用'
}

type McpTarget = {
  id: string
  server_name: string
  remark: string | null
  has_fixed_token: boolean
  bindable_slots: string[]
  bindings: Record<string, Binding | null>
  /** 旧字段：只有 Authorization 的那一个绑定 */
  binding: Binding | null
}

type ToolTarget = {
  id: string
  name: string
  description: string | null
  bindable_slots: string[]
  bindings: Record<string, Binding | null>
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
              </div>
            </div>

            <button
              v-else
              type="button"
              class="shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50"
              @click="openBindMcp(item)"
            >{{ item.binding ? '更换凭据' : '绑定我的凭据' }}</button>
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

          <div class="mt-3">
            <CredentialPicker v-model="secretId" scene="mcp" @picked="onPicked" />
          </div>

          <div v-if="secretId" class="mt-2 space-y-1">
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
              {{ busy ? '保存中…' : secretId ? '保存绑定' : '解除绑定' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
