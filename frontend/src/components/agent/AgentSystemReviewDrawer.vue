<script setup lang="ts">
/**
 * 发布审核：左侧待审队列，右侧只读快照 + 通过/驳回。
 *
 * 审核的定位是「看内容、做判断、留痕」，不是二次创作：
 * 快照一律只读，通过时不改任何版本状态、不跑 readiness——上架只是把
 * is_system 置 true，运行时用哪个版本仍由既有的「发布版本」流程决定。
 */
import { computed, ref, watch } from 'vue'
import type { AgentSystemRequestDetail, AgentSystemRequestItem } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ isOpen: boolean }>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'done'): void
  (e: 'changed'): void
}>()

const { showToast } = useToast()

const items = ref<AgentSystemRequestItem[]>([])
const detail = ref<AgentSystemRequestDetail | null>(null)
const selectedId = ref<string>('')
const keyword = ref('')
const sortMode = ref<'oldest' | 'newest' | 'late'>('oldest')
const loadingList = ref(false)
const loadingDetail = ref(false)
const reviewComment = ref('')
const submitting = ref(false)
const errorMessage = ref('')

const visibleItems = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  let list = items.value
  if (text) {
    list = list.filter(
      (item) =>
        (item.agent_name || '').toLowerCase().includes(text) ||
        (item.agent_identifier || '').toLowerCase().includes(text),
    )
  }
  if (sortMode.value === 'newest') {
    return [...list].reverse()
  }
  if (sortMode.value === 'late') {
    return list.filter((item) => item.waiting_hours >= 24)
  }
  return list
})

const waitingLabel = (hours: number) => {
  if (hours >= 24) return `等待 ${Math.floor(hours / 24)} 天`
  if (hours >= 1) return `等待 ${Math.round(hours)} 小时`
  return '刚刚提交'
}

const isLate = (hours: number) => hours >= 24

const formatTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

const loadList = async () => {
  loadingList.value = true
  // 刷新成功时不清旧错误，会让「加载待审列表失败」一直挂在详情下方
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.listRequests({ status: 'PENDING', page_size: 100 })
    items.value = res.data || []
    if (items.value.length) {
      const stillThere = items.value.some((item) => item.id === selectedId.value)
      if (!stillThere) await select(items.value[0].id)
    } else {
      selectedId.value = ''
      detail.value = null
    }
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '加载待审列表失败'
  } finally {
    loadingList.value = false
  }
}

// 点 A 再点 B 时，若 A 的响应后到就会把右侧覆盖成 A，而左侧高亮仍是 B。
// 审核人于是可能对着「以为在看的 B」点了通过/驳回，实际处理的是 A。
let selectSeq = 0
const select = async (requestId: string) => {
  const seq = ++selectSeq
  selectedId.value = requestId
  loadingDetail.value = true
  reviewComment.value = ''
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.getRequest(requestId)
    if (seq !== selectSeq) return
    detail.value = res.data
  } catch (error: any) {
    if (seq !== selectSeq) return
    detail.value = null
    errorMessage.value = error?.response?.data?.detail || '加载申请详情失败'
  } finally {
    if (seq === selectSeq) loadingDetail.value = false
  }
}

watch(
  () => props.isOpen,
  (open) => {
    if (!open) return
    items.value = []
    detail.value = null
    selectedId.value = ''
    keyword.value = ''
    sortMode.value = 'oldest'
    errorMessage.value = ''
    void loadList()
  },
)

const isVersionRelease = computed(() => detail.value?.request_type === 'VERSION_RELEASE')

// 两种申请单的通过后果完全不同：转系统级是「进入委派池」，版本上线是「换个基线」
const approveToast = computed(() =>
  isVersionRelease.value
    ? `已通过，V${detail.value?.snapshot?.version?.version_number ?? ''} 已上线`
    : '已通过并发布为系统级'
)
const approveHint = computed(() =>
  isVersionRelease.value
    ? '通过后该版本立即成为线上基线，被授权用户与主助手都会用到新配置；旧版本归档。'
    : '该智能体马上进入主助手的自动委派候选与路由，被授权用户可用。请确认配置与工具已就绪。'
)
const approveButtonLabel = computed(() => (isVersionRelease.value ? '通过并上线' : '通过并发布'))

const approve = async () => {
  if (!detail.value || submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    await agentSystemApi.approveRequest(detail.value.id, {
      comment: reviewComment.value.trim() || undefined,
    })
    showToast(approveToast.value, 'success')
    emit('done')
    emit('changed')
    await loadList()
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '审核失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

const reject = async () => {
  if (!detail.value || submitting.value) return
  const reason = reviewComment.value.trim()
  if (!reason) {
    errorMessage.value = '驳回时必须填写理由，申请人会看到这段说明'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    await agentSystemApi.rejectRequest(detail.value.id, { comment: reason })
    showToast('已驳回，智能体已解冻', 'success')
    emit('changed')
    await loadList()
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '驳回失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-[100] flex justify-end bg-slate-900/40">
      <div class="flex h-full w-full max-w-4xl flex-col overflow-hidden bg-white shadow-2xl">
        <div class="flex items-center gap-2 border-b border-slate-200 px-5 py-3">
          <h2 class="text-base font-semibold text-slate-900">发布审核</h2>
          <span
            class="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-200"
          >
            {{ items.length }} 条待审
          </span>
          <button
            class="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>

        <div class="flex min-h-0 flex-1">
          <!-- 左：待审队列 -->
          <div class="flex w-72 shrink-0 flex-col border-r border-slate-200 bg-slate-50">
            <div class="space-y-2 border-b border-slate-200 bg-white px-3 py-2.5">
              <input
                v-model="keyword"
                type="search"
                placeholder="搜索申请人或智能体…"
                class="w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-primary"
              />
              <div class="inline-flex overflow-hidden rounded-md border border-slate-200 text-[11px]">
                <button
                  v-for="option in [
                    { key: 'oldest', label: '最久优先' },
                    { key: 'newest', label: '最新' },
                    { key: 'late', label: '仅超 24h' },
                  ]"
                  :key="option.key"
                  class="px-2.5 py-1"
                  :class="
                    sortMode === option.key
                      ? 'bg-primary/10 font-semibold text-primary'
                      : 'text-slate-500 hover:bg-slate-50'
                  "
                  @click="sortMode = option.key as typeof sortMode"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto">
              <p v-if="loadingList" class="p-4 text-xs text-slate-400">加载中…</p>
              <p
                v-else-if="!visibleItems.length"
                class="p-4 text-xs leading-relaxed text-slate-400"
              >
                暂无待审申请。
              </p>
              <button
                v-for="item in visibleItems"
                :key="item.id"
                class="w-full border-b border-slate-100 px-3 py-2.5 text-left hover:bg-white"
                :class="selectedId === item.id ? 'bg-white shadow-[inset_3px_0_0_#1677ff]' : ''"
                @click="select(item.id)"
              >
                <p class="flex items-center gap-1.5 text-[13px] font-semibold text-slate-800">
                  <span class="truncate">{{ item.agent_name }}</span>
                  <span
                    class="shrink-0 rounded px-1 py-0.5 text-[10px] font-medium"
                    :class="item.request_type === 'VERSION_RELEASE'
                      ? 'bg-sky-50 text-sky-700'
                      : 'bg-indigo-50 text-indigo-600'"
                  >
                    {{ item.request_type === 'VERSION_RELEASE'
                      ? (item.version_number ? `V${item.version_number} 上线` : '版本上线')
                      : '转系统级' }}
                  </span>
                </p>
                <p class="mt-0.5 flex flex-wrap gap-2 text-[11px] text-slate-500">
                  <span>{{ item.requested_by_name || '申请人' }} 提交</span>
                  <span :class="isLate(item.waiting_hours) ? 'font-semibold text-amber-600' : ''">
                    {{ waitingLabel(item.waiting_hours) }}
                  </span>
                </p>
                <p class="mt-0.5 text-[11px] text-slate-400">
                  <template v-if="item.agent_missing">
                    <span class="text-rose-600">智能体已删除，请直接驳回关闭</span>
                  </template>
                  <template v-else>
                    {{ item.engine_type || 'LOCAL' }} · {{ item.agent_identifier }}
                  </template>
                </p>
              </button>
            </div>
          </div>

          <!-- 右：只读快照 + 审核动作 -->
          <div class="flex min-h-0 flex-1 flex-col">
            <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <p v-if="loadingDetail" class="text-xs text-slate-400">加载详情…</p>
              <p v-else-if="!detail" class="text-xs text-slate-400">从左侧选择一条申请。</p>

              <div v-else class="space-y-3">
                <div class="flex items-start gap-3">
                  <div>
                    <p class="text-lg font-semibold text-slate-900">{{ detail.agent_name }}</p>
                    <p class="mt-1 text-xs text-slate-500">
                      申请人 {{ detail.requested_by_name || '—' }} ·
                      提交于 {{ formatTime(detail.requested_at) }} ·
                      {{ detail.engine_type || 'LOCAL' }}
                    </p>
                  </div>
                </div>

                <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900">
                  ⚠ <strong>通过后将立即生效</strong>：{{ approveHint }}
                </div>

                <div v-if="detail.request_comment">
                  <p class="mb-1 text-xs font-semibold text-slate-700">申请人理由</p>
                  <p class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
                    {{ detail.request_comment }}
                  </p>
                </div>

                <div v-if="detail.snapshot" class="space-y-2">
                  <p class="text-xs font-semibold text-slate-700">提交时的内容快照（只读）</p>

                  <div class="overflow-hidden rounded-lg border border-slate-200">
                    <div class="border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-[11px] font-semibold text-slate-600">
                      元数据
                    </div>
                    <dl class="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 px-3 py-2 text-xs">
                      <dt class="text-slate-400">显示名称</dt>
                      <dd class="break-all text-slate-700">{{ detail.snapshot.agent?.display_name }}</dd>
                      <dt class="text-slate-400">标识符</dt>
                      <dd class="break-all text-slate-700">{{ detail.snapshot.agent?.name }}</dd>
                      <dt class="text-slate-400">描述</dt>
                      <dd class="break-all text-slate-700">{{ detail.snapshot.agent?.description || '—' }}</dd>
                      <dt class="text-slate-400">引擎</dt>
                      <dd class="text-slate-700">{{ detail.snapshot.agent?.engine_type || 'LOCAL' }}</dd>
                    </dl>
                  </div>

                  <div v-if="detail.snapshot.version" class="overflow-hidden rounded-lg border border-slate-200">
                    <div class="border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-[11px] font-semibold text-slate-600">
                      版本 v{{ detail.snapshot.version.version_number }} · 模型与工具
                    </div>
                    <dl class="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 px-3 py-2 text-xs">
                      <dt class="text-slate-400">模型</dt>
                      <dd class="text-slate-700">
                        {{ detail.snapshot.version.model_name || '—' }} · 温度
                        {{ detail.snapshot.version.temperature ?? 0 }}
                      </dd>
                      <dt class="text-slate-400">工具</dt>
                      <dd class="flex flex-wrap gap-1">
                        <span
                          v-for="tool in detail.snapshot.version.tools || []"
                          :key="typeof tool === 'string' ? tool : tool?.name"
                          class="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-700"
                        >
                          {{ typeof tool === "string" ? tool : tool?.name || "未命名工具" }}
                        </span>
                        <span
                          v-if="!(detail.snapshot.version.tools || []).length"
                          class="text-slate-400"
                        >
                          未绑定工具
                        </span>
                      </dd>
                    </dl>
                  </div>

                  <div v-if="detail.snapshot.version" class="overflow-hidden rounded-lg border border-slate-200">
                    <div class="border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-[11px] font-semibold text-slate-600">
                      系统提示词（只读）
                    </div>
                    <div class="px-3 py-2">
                      <textarea
                        readonly
                        rows="6"
                        class="w-full resize-y rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-slate-600 outline-none"
                        :value="detail.snapshot.version.system_prompt || ''"
                      ></textarea>
                    </div>
                  </div>
                </div>

                <div>
                  <p class="mb-1 text-xs font-semibold text-slate-700">
                    审核意见
                    <span class="ml-1 font-normal text-slate-400">驳回时必填</span>
                  </p>
                  <textarea
                    v-model="reviewComment"
                    rows="3"
                    maxlength="500"
                    class="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10"
                    placeholder="例如：工具 doc_export 依赖个人凭证，请先换成公共工具或移除后再提交"
                  ></textarea>
                </div>

                <p v-if="errorMessage" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                  {{ errorMessage }}
                </p>
              </div>
            </div>

            <div
              v-if="detail"
              class="flex items-center gap-2 border-t border-slate-200 bg-white px-5 py-3"
            >
              <span class="mr-auto text-[11px] text-slate-400">
                <template v-if="detail.agent_missing">
                  对应智能体已删除，这条申请只能驳回关闭
                </template>
                <template v-else>
                  通过后会记一条发布事件，申请人会收到通知
                </template>
              </span>
              <button
                class="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                :disabled="submitting"
                @click="reject"
              >
                {{ detail.agent_missing ? '驳回并关闭' : '驳回' }}
              </button>
              <button
                v-if="!detail.agent_missing"
                class="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:opacity-50"
                :disabled="submitting"
                @click="approve"
              >
                {{ approveButtonLabel }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
