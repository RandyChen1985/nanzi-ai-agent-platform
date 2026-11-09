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
import axios from '../utils/axios'

interface Grant {
  id: string
  grantee_type: 'user' | 'dept'
  grantee_id: string
  expires_at: string | null
}

interface SystemSecret {
  id: string
  secret_key: string
  name: string
  secret_type: 'credential' | 'variable'
  status: string
  allowed_scenarios: string[]
  allowed_hosts: string[]
  value_masked: string
  grants: Grant[]
  grant_count: number
  last_rotated_at: string | null
}

const SCENE_LABEL: Record<string, string> = {
  mcp: 'MCP',
  api_tool: 'API 工具',
  script_env: '脚本',
  scheduled_task: '定时任务',
  agent: '智能体',
}

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
const createBusy = ref(false)
const form = ref({
  secret_key: '',
  name: '',
  value: '',
  secret_type: 'credential' as 'credential' | 'variable',
  allowed_scenarios: ['mcp'] as string[],
  allowed_hosts: '',
})

function openCreate() {
  form.value = {
    secret_key: '',
    name: '',
    value: '',
    secret_type: 'credential',
    allowed_scenarios: ['mcp'],
    allowed_hosts: '',
  }
  showCreate.value = true
}

function toggleScene(key: string) {
  const idx = form.value.allowed_scenarios.indexOf(key)
  if (idx >= 0) form.value.allowed_scenarios.splice(idx, 1)
  else form.value.allowed_scenarios.push(key)
}

async function submitCreate() {
  if (!form.value.secret_key.trim()) return showToast('请填写凭据标识', 'err')
  if (!form.value.value) return showToast('请填写凭据值', 'err')
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
const grantForm = ref({ grantee_type: 'user' as 'user' | 'dept', grantee_id: '' })

function openGrant(item: SystemSecret) {
  grantTarget.value = item
  grantForm.value = { grantee_type: 'user', grantee_id: '' }
}

async function submitGrant() {
  if (!grantTarget.value || !grantForm.value.grantee_id.trim()) return
  grantBusy.value = true
  try {
    await axios.post(`/api/portal/secrets/admin/${grantTarget.value.id}/grants`, {
      grantee_type: grantForm.value.grantee_type,
      grantee_id: grantForm.value.grantee_id.trim(),
    })
    grantTarget.value = null
    showToast('已授权')
    await load()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '授权失败', 'err')
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

    <div v-if="stats.noGrant" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800">
      有 <strong>{{ stats.noGrant }}</strong> 条系统级凭据处于「生效中但没有任何授权」。它们对除管理员外的所有人不可见，也不会有任何调用能用到它们。
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
    <div v-else class="space-y-2">
      <div
        v-for="item in filtered"
        :key="item.id"
        class="rounded-xl border border-gray-200 bg-white p-3.5"
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
                v-for="scene in item.allowed_scenarios"
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
            {{ grant.grantee_type === 'dept' ? '部门' : '用户' }} · {{ grant.grantee_id }}
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
      </div>
    </div>

    <p class="text-[11px] leading-relaxed text-gray-400">
      管理员也读不到凭据明文——这不是权限设置，是平台没有这个能力。要更换值只能让创建者轮换，或停用后重建。
    </p>

    <!-- 新建 -->
    <Teleport to="body">
          <div
            v-if="showCreate"
            class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
            @click.self="showCreate = false"
          >
            <div class="flex max-h-[calc(100vh-120px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
              <div class="border-b border-gray-100 px-5 py-3">
                <h3 class="text-base font-semibold text-gray-900">新建系统级凭据</h3>
                <p class="mt-0.5 text-xs text-gray-500">创建后需要单独授权，否则没有人能用</p>
              </div>
              <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
                <label class="block">
                  <span class="text-xs font-medium text-gray-700">凭据标识</span>
                  <input
                    v-model="form.secret_key"
                    placeholder="corp.wecom.secret"
                    class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label class="block">
                  <span class="text-xs font-medium text-gray-700">名称</span>
                  <input
                    v-model="form.name"
                    class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label class="block">
                  <span class="text-xs font-medium text-gray-700">凭据值</span>
                  <input
                    v-model="form.value"
                    type="password"
                    autocomplete="new-password"
                    class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <div>
                  <span class="text-xs font-medium text-gray-700">允许注入的场景</span>
                  <div class="mt-1 flex flex-wrap gap-1.5">
                    <!-- 只列已接通的链路：定时任务/智能体还没有注入代码，勾了是空承诺 -->
                    <button
                      v-for="key in ['mcp', 'api_tool', 'script_env']"
                      :key="key"
                      type="button"
                      class="rounded-lg border px-2.5 py-1 text-xs"
                      :class="form.allowed_scenarios.includes(key) ? 'border-blue-400 bg-blue-50 text-blue-700' : 'border-gray-200 text-gray-600'"
                      @click="toggleScene(key)"
                    >
                      {{ SCENE_LABEL[key] }}
                    </button>
                    <span class="self-center text-[11px] text-gray-400">定时任务 / 智能体暂未接入</span>
                  </div>
                </div>
                <label class="block">
                  <span class="text-xs font-medium text-gray-700">域名白名单</span>
                  <input
                    v-model="form.allowed_hosts"
                    placeholder="qyapi.weixin.qq.com"
                    class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </label>
              </div>
              <div class="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="showCreate = false">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="createBusy"
                  class="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
                  @click="submitCreate"
                >
                  {{ createBusy ? '创建中…' : '确认创建' }}
                </button>
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
            <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
              <h3 class="text-base font-semibold text-gray-900">授权凭据</h3>
              <p class="mt-1 text-xs text-gray-500">
                <code>{{ grantTarget.secret_key }}</code> 授权给谁。被授权的人才能看见并使用它。
              </p>
              <div class="mt-3 space-y-2">
                <div class="inline-flex rounded-lg bg-gray-100 p-1">
                  <button
                    type="button"
                    class="rounded-md px-2.5 py-1 text-xs"
                    :class="grantForm.grantee_type === 'user' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
                    @click="grantForm.grantee_type = 'user'"
                  >
                    用户
                  </button>
                  <button
                    type="button"
                    class="rounded-md px-2.5 py-1 text-xs"
                    :class="grantForm.grantee_type === 'dept' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
                    @click="grantForm.grantee_type = 'dept'"
                  >
                    部门
                  </button>
                </div>
                <input
                  v-model="grantForm.grantee_id"
                  :placeholder="grantForm.grantee_type === 'user' ? '用户 ID' : '部门编码'"
                  class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div class="mt-4 flex justify-end gap-2">
                <button type="button" class="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100" @click="grantTarget = null">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="grantBusy"
                  class="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                  @click="submitGrant"
                >
                  {{ grantBusy ? '授权中…' : '确认授权' }}
                </button>
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
