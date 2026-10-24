<script setup lang="ts">
/**
 * 分享给我：别人发来的智能体副本邀请。
 *
 * 与技能 / MCP 分享保持同一套交互（页面级视图 + 待处理筛选 + 接受/拒绝），
 * 不再用弹窗——弹窗入口「有待处理才出现」，对方根本不知道去哪儿找。
 *
 * 接受时必须重新命名：物理标识符全局唯一，直接沿用源名称必然冲突。
 * 名字留空由服务端生成不冲突的 `原名-copy`。
 */
import { computed, onMounted, ref } from 'vue'
import type { AgentShareRecord } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { useToast } from '../../composables/useToast'

const emit = defineEmits<{
  (e: 'updated'): void
  (e: 'accepted', agentId: string): void
}>()

const { showToast } = useToast()

const items = ref<AgentShareRecord[]>([])
const loading = ref(false)
const errorMessage = ref('')
const filter = ref<'PENDING' | 'all'>('PENDING')
const actingId = ref<string | null>(null)

const expanded = ref<Record<string, boolean>>({})
const toggleExpand = (id: string) => {
  expanded.value = { ...expanded.value, [id]: !expanded.value[id] }
}

const naming = ref<AgentShareRecord | null>(null)
const newName = ref('')
const newDisplayName = ref('')
const acceptError = ref('')

const pendingCount = computed(() => items.value.filter((item) => item.status === 'PENDING').length)

/** 搜索词来自父级搜索框：面板自己没有输入框，靠外部关键词过滤 */
const props = withDefaults(defineProps<{ keyword?: string }>(), { keyword: '' })

const byStatus = computed(() =>
  filter.value === 'PENDING'
    ? items.value.filter((item) => item.status === 'PENDING')
    : items.value,
)

const visible = computed(() => {
  const kw = (props.keyword || '').trim().toLowerCase()
  if (!kw) return byStatus.value
  return byStatus.value.filter((item) =>
    [item.agent_name, item.agent_display_name, item.owner_user_name, item.message]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(kw)),
  )
})

// 快速切「待处理 / 全部」时，先发的请求可能后返回，把列表串成另一个筛选条件的结果
let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.listReceivedShares(
      filter.value === 'PENDING' ? { only_pending: true } : undefined,
    )
    if (seq !== loadSeq) return
    items.value = res.data || []
  } catch (e: any) {
    if (seq !== loadSeq) return
    errorMessage.value = e?.response?.data?.detail || '加载失败，请稍后重试'
    items.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)

const switchFilter = async (next: 'PENDING' | 'all') => {
  filter.value = next
  await load()
}

const statusMeta = (status: string): { label: string; className: string } => {
  switch (status) {
    case 'PENDING':
      return { label: '待处理', className: 'bg-amber-50 text-amber-700 border-amber-200' }
    case 'ACCEPTED':
      return { label: '已接受', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
    case 'REJECTED':
      return { label: '已拒绝', className: 'bg-gray-50 text-gray-600 border-gray-200' }
    case 'EXPIRED':
      return { label: '已过期', className: 'bg-gray-50 text-gray-500 border-gray-200' }
    case 'REVOKED':
      return { label: '已作废', className: 'bg-rose-50 text-rose-700 border-rose-200' }
    case 'WITHDRAWN':
      return { label: '已撤回', className: 'bg-gray-50 text-gray-600 border-gray-200' }
    default:
      return { label: status, className: 'bg-gray-50 text-gray-600 border-gray-200' }
  }
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** 默认名只是给用户一个起点，留空服务端也会生成 */
const suggestedName = (record: AgentShareRecord) => {
  const base = (record.agent_name || record.agent_display_name || 'agent')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40) || 'agent'
  return `${base}-copy`
}

const openNaming = (record: AgentShareRecord) => {
  naming.value = record
  newName.value = suggestedName(record)
  newDisplayName.value = record.agent_display_name || record.agent_name || ''
  acceptError.value = ''
}

const submitAccept = async () => {
  if (!naming.value) return
  const target = naming.value
  actingId.value = target.id
  acceptError.value = ''
  try {
    const res = await agentSystemApi.acceptShare(target.id, {
      name: newName.value.trim() || undefined,
      display_name: newDisplayName.value.trim() || undefined,
    })
    naming.value = null
    showToast('已生成副本，继续配置后即可发布', 'success')
    await load()
    emit('updated')
    emit('accepted', res.data.id)
  } catch (e: any) {
    // detail 可能是字符串，也可能是 422 的校验错误数组，直接塞进字符串会显示 [object Object]
    const detail = e?.response?.data?.detail
    acceptError.value =
      typeof detail === 'string' ? detail : Array.isArray(detail) ? (detail[0]?.msg ?? '参数不合法') : '接受失败，请稍后重试'
  } finally {
    actingId.value = null
  }
}

const reject = async (record: AgentShareRecord) => {
  actingId.value = record.id
  try {
    await agentSystemApi.rejectShare(record.id)
    showToast('已拒绝该分享', 'success')
    await load()
    emit('updated')
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '拒绝失败', 'error')
  } finally {
    actingId.value = null
  }
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-1.5">
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          :class="filter === 'PENDING' ? 'bg-primary text-white' : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'"
          @click="switchFilter('PENDING')"
        >
          待处理
          <span v-if="pendingCount" class="ml-1 font-bold">{{ pendingCount }}</span>
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          :class="filter === 'all' ? 'bg-primary text-white' : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'"
          @click="switchFilter('all')"
        >
          全部
        </button>
      </div>
      <button
        type="button"
        class="text-xs font-medium text-gray-500 hover:text-primary"
        @click="load"
      >
        刷新
      </button>
    </div>

    <div v-if="loading" class="rounded-xl border border-gray-200 bg-white py-12 text-center text-sm text-gray-400">
      正在加载…
    </div>
    <div v-else-if="errorMessage" class="rounded-xl border border-red-100 bg-white py-12 text-center text-sm text-red-600">
      {{ errorMessage }}
    </div>
    <div v-else-if="!visible.length" class="rounded-xl border border-gray-200 bg-white py-12 text-center text-sm text-gray-400">
      {{ filter === 'PENDING' ? '暂时没有人分享智能体给你' : '还没有收到过分享' }}
    </div>

    <div v-else class="space-y-2.5">
      <div
        v-for="record in visible"
        :key="record.id"
        class="rounded-xl border border-gray-200 bg-white p-4 transition-colors hover:border-gray-300"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <p class="truncate text-sm font-semibold text-gray-900">
                {{ record.agent_display_name || record.agent_name }}
              </p>
              <span
                class="rounded border px-1.5 py-0.5 text-[11px] font-medium"
                :class="statusMeta(record.status).className"
              >
                {{ statusMeta(record.status).label }}
              </span>
            </div>
            <p class="mt-1 text-xs text-gray-500">
              {{ record.owner_user_name || '同事' }} 分享于 {{ formatDateTime(record.created_at) }}
              <span v-if="record.status === 'PENDING' && record.expires_at" class="text-amber-600">
                · {{ formatDateTime(record.expires_at) }} 前有效
              </span>
            </p>

            <!-- 摘要：只说「谁分享了一个叫 dddd 的东西」没法让人判断要不要接受 -->
            <p v-if="record.summary?.description" class="mt-1.5 text-xs text-gray-600">
              {{ record.summary.description }}
            </p>
            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <span
                v-if="record.summary?.model_name"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                模型 {{ record.summary.model_name }}
              </span>
              <span
                v-if="record.summary?.tool_count"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                {{ record.summary.tool_count }} 个工具
              </span>
              <span
                v-if="record.summary?.skill_count"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                {{ record.summary.skill_count }} 个技能
              </span>
              <span
                v-if="record.summary?.source_version_number"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                源版本 v{{ record.summary.source_version_number }}
              </span>
              <span
                v-if="record.summary?.prompt_length"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                提示词 {{ record.summary.prompt_length }} 字
              </span>
            </div>
            <div
              v-if="record.summary?.tool_names?.length"
              class="mt-1.5 flex flex-wrap gap-1"
            >
              <span
                v-for="name in record.summary.tool_names.slice(0, 6)"
                :key="name"
                class="rounded bg-primary/5 px-1.5 py-0.5 text-[11px] text-primary/80"
              >
                {{ name }}
              </span>
              <span
                v-if="record.summary.tool_names.length > 6"
                class="px-1 py-0.5 text-[11px] text-gray-400"
              >
                等 {{ record.summary.tool_names.length }} 个工具
              </span>
            </div>

            <!-- 提示词：折叠时给开头，展开看全文。接受前要能判断这东西靠不靠谱 -->
            <div
              v-if="record.summary?.system_prompt"
              class="mt-1.5 rounded-lg bg-gray-50 px-2.5 py-1.5"
            >
              <p
                class="text-[11px] leading-relaxed text-gray-500"
                :class="expanded[record.id] ? 'whitespace-pre-wrap max-h-56 overflow-y-auto' : 'line-clamp-2'"
              >
                {{ record.summary.system_prompt }}
              </p>
              <button
                v-if="(record.summary.system_prompt?.length || 0) > 80"
                type="button"
                class="mt-1 text-[11px] font-medium text-primary hover:underline"
                @click="toggleExpand(record.id)"
              >
                {{ expanded[record.id] ? '收起' : '查看完整提示词' }}
              </button>
            </div>
            <p
              v-if="record.share_message"
              class="mt-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs text-gray-600"
            >
              {{ record.share_message }}
            </p>
            <p v-if="record.share_reason" class="mt-1.5 text-xs text-gray-500">
              {{ record.share_reason }}
            </p>
          </div>

          <div v-if="record.status === 'PENDING'" class="flex shrink-0 gap-1.5">
            <button
              type="button"
              :disabled="actingId === record.id"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              @click="reject(record)"
            >
              拒绝
            </button>
            <button
              type="button"
              :disabled="actingId === record.id"
              class="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-dark disabled:opacity-50"
              @click="openNaming(record)"
            >
              接受
            </button>
          </div>
        </div>

        <!-- 命名：接受必须重新命名，物理标识符全局唯一 -->
        <div v-if="naming?.id === record.id" class="mt-3 rounded-lg border border-primary/20 bg-primary/5 p-3">
          <p class="text-xs text-gray-600">
            接受后会生成 <strong>属于你自己的副本</strong>，需要重新命名并确认配置；你的源智能体不受影响。
          </p>
          <div class="mt-2 grid gap-2 sm:grid-cols-2">
            <input
              v-model="newName"
              type="text"
              placeholder="物理标识符，留空自动生成"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <input
              v-model="newDisplayName"
              type="text"
              placeholder="显示名称"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <p v-if="acceptError" class="mt-2 text-xs text-red-600">{{ acceptError }}</p>
          <div class="mt-2 flex justify-end gap-2">
            <button
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50"
              @click="naming = null"
            >
              取消
            </button>
            <button
              type="button"
              :disabled="actingId === record.id"
              class="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-dark disabled:opacity-50"
              @click="submitAccept"
            >
              确认接受
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
