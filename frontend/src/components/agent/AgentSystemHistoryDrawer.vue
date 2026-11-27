<script setup lang="ts">
/**
 * 发布历史：申请与事件合并成一条时间线，并按自然日分组展示。
 *
 * 只对管理员和该智能体的所有者开放，没有跨智能体的全局视图——这个页面的
 * 用途是「这个智能体为什么被上架/下架」，而不是审计全平台。
 *
 * 分组是为了跨天可读：一天里往往连着「提交申请 → 审核通过 → 发基线」好几条，
 * 混在一条连续时间线里既看不出是哪天的事，也没法先折叠再按天展开回看。
 * 分组规则（日期标签、版本汇总、`at` 缺失兜底）放在 utils/agentPublishHistory.ts，
 * 由 node 行为测试覆盖。
 */
import { computed, ref, watch } from 'vue'
import type { AIAgent, AgentSystemHistoryEntry } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { collectVersionNumbers, groupEntriesByDay } from '../../utils/agentPublishHistory'

const props = defineProps<{
  isOpen: boolean
  agent: AIAgent | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

// 版本里的 tools 可能是纯字符串，也可能是 { name, ... } 配置对象；
// 直接 join 会把对象渲染成 [object Object]
const formatToolNames = (tools: unknown): string => {
  if (!Array.isArray(tools)) return ""
  return tools
    .map((item: any) => (typeof item === "string" ? item : item?.name || ""))
    .filter((name: string) => !!name)
    .join("、")
}

const entries = ref<AgentSystemHistoryEntry[]>([])
const totalCount = ref(0)
const loading = ref(false)
const errorMessage = ref('')
const expanded = ref<string>('')

/** 折叠的日期键集合；只存 `true`，避免「全部展开」后再折叠时残留旧键。 */
const collapsedDays = ref<Record<string, boolean>>({})

const COLLAPSED_STORAGE_PREFIX = 'nanzi:agent-publish-history:collapsed-days'

const storageKey = () => `${COLLAPSED_STORAGE_PREFIX}:${props.agent?.id || 'unknown'}`

const readCollapsedDays = (): Record<string, boolean> | null => {
  try {
    const raw = sessionStorage.getItem(storageKey())
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const restored: Record<string, boolean> = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (value === true) restored[key] = true
    }
    return restored
  } catch {
    // 隐私模式 / 存储被禁用：当成没有记忆，不影响主流程
    return null
  }
}

const persistCollapsedDays = () => {
  try {
    sessionStorage.setItem(storageKey(), JSON.stringify(collapsedDays.value))
  } catch {
    /* 忽略：记忆只是锦上添花 */
  }
}

const dayGroups = computed(() =>
  groupEntriesByDay(entries.value).map((group) => ({
    ...group,
    versions: collectVersionNumbers(group.items),
  })),
)

const isCollapsed = (key: string) => collapsedDays.value[key] === true

const toggleDay = (key: string) => {
  if (isCollapsed(key)) {
    const next = { ...collapsedDays.value }
    delete next[key]
    collapsedDays.value = next
  } else {
    collapsedDays.value = { ...collapsedDays.value, [key]: true }
  }
  persistCollapsedDays()
}

const setAllDaysCollapsed = (collapse: boolean) => {
  if (!collapse) {
    collapsedDays.value = {}
  } else {
    const next: Record<string, boolean> = {}
    for (const group of dayGroups.value) next[group.key] = true
    collapsedDays.value = next
  }
  persistCollapsedDays()
}

/**
 * 默认只展开最近一天，其余折叠——历史最多 100 条，全展开仍要长滚动；
 * 同一次会话内则沿用用户手动折叠过的结果（按智能体分别记）。
 */
const applyDefaultCollapse = () => {
  const restored = readCollapsedDays()
  if (restored) {
    collapsedDays.value = restored
    return
  }
  const next: Record<string, boolean> = {}
  dayGroups.value.forEach((group, index) => {
    if (index > 0) next[group.key] = true
  })
  collapsedDays.value = next
}

const formatTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

const eventTone = (entry: AgentSystemHistoryEntry) => {
  if (entry.kind === 'request') {
    if (entry.status === 'APPROVED') return { dot: 'bg-emerald-500', label: '申请已通过' }
    if (entry.status === 'REJECTED') return { dot: 'bg-red-500', label: '申请被驳回' }
    if (entry.status === 'CANCELLED') return { dot: 'bg-slate-400', label: '申请人撤回' }
    // 版本上线申请与转系统级申请是两件事，标签不能一句话糊过去
    return {
      dot: 'bg-amber-500',
      label: entry.request_type === 'VERSION_RELEASE' ? '提交版本上线申请' : '提交发布申请',
    }
  }
  const map: Record<string, { dot: string; label: string }> = {
    PUBLISH: { dot: 'bg-emerald-500', label: '发布为系统级' },
    UNPUBLISH: { dot: 'bg-red-500', label: '下架（取消系统级）' },
    META_UPDATE: { dot: 'bg-primary', label: '修改元数据' },
    TOGGLE_ENABLED: { dot: 'bg-slate-500', label: '启用状态变更' },
    BASELINE_PUBLISH: { dot: 'bg-primary', label: '发布新基线版本' },
    TRANSFER_OWNER: { dot: 'bg-primary', label: '变更权属人' },
  SHARE_SEND: { dot: 'bg-primary', label: '分享给同事' },
  SHARE_ACCEPT: { dot: 'bg-primary', label: '分享被接受' },
  SHARE_REJECT: { dot: 'bg-gray-400', label: '分享被拒绝' },
  SHARE_REVOKE: { dot: 'bg-amber-500', label: '分享已作废' },
  }
  return map[entry.action || ''] || { dot: 'bg-slate-400', label: entry.action || '操作' }
}

const toggle = (key: string) => {
  expanded.value = expanded.value === key ? '' : key
}

const load = async () => {
  if (!props.agent) return
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.history(props.agent.id, { page_size: 100 })
    entries.value = res.data.items || []
    // 超过 100 条会被静默截断：用户以为「最早就到这儿」，其实是没翻页入口
    totalCount.value = res.data.total ?? entries.value.length
    applyDefaultCollapse()
  } catch (error: any) {
    entries.value = []
    errorMessage.value = error?.response?.data?.detail || '加载发布历史失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.isOpen,
  (open) => {
    if (!open) return
    entries.value = []
    expanded.value = ''
    collapsedDays.value = {}
    void load()
  },
)
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-[100] flex justify-end bg-slate-900/40">
      <div class="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
        <div class="flex items-center gap-2 border-b border-slate-200 px-5 py-3">
          <div>
            <h2 class="text-base font-semibold text-slate-900">发布历史</h2>
            <p class="mt-0.5 text-xs text-slate-500">{{ agent?.display_name }}</p>
          </div>
          <button
            class="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>

        <div
          v-if="!loading && !errorMessage && entries.length"
          class="flex items-center gap-2 border-b border-slate-100 bg-slate-50/70 px-5 py-2 text-[11px] text-slate-400"
        >
          <span>
            <template v-if="totalCount > entries.length">
              仅显示最近 {{ entries.length }} 条（共 {{ totalCount }} 条）
            </template>
            <template v-else>{{ entries.length }} 条记录 · 按日期分组</template>
          </span>
          <span class="ml-auto"></span>
          <button
            class="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:border-primary hover:text-primary"
            @click="setAllDaysCollapsed(false)"
          >
            全部展开
          </button>
          <button
            class="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:border-primary hover:text-primary"
            @click="setAllDaysCollapsed(true)"
          >
            全部收起
          </button>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <p v-if="loading" class="text-xs text-slate-400">加载中…</p>
          <p v-else-if="errorMessage" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
            {{ errorMessage }}
          </p>
          <p v-else-if="!entries.length" class="text-xs leading-relaxed text-slate-400">
            还没有发布记录。该智能体被设为系统级后，这里会记录每次申请、审核、上架、下架与配置变更。
          </p>

          <div v-else>
            <section
              v-for="group in dayGroups"
              :key="group.key"
              class="mt-1 first:mt-0"
            >
              <!-- 组头整行可点：日期 + 条数 + 当天涉及的版本号，折叠时补一句「最近一条是什么」 -->
              <button
                type="button"
                class="flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left hover:bg-slate-50"
                :aria-expanded="!isCollapsed(group.key)"
                @click="toggleDay(group.key)"
              >
                <svg
                  class="h-3.5 w-3.5 flex-none text-slate-400 transition-transform"
                  :class="isCollapsed(group.key) ? '' : 'rotate-90'"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="M6 3.5 10.5 8 6 12.5" />
                </svg>
                <span class="text-[13px] font-semibold text-slate-800">{{ group.label }}</span>
                <span v-if="group.weekday" class="text-[11.5px] font-normal text-slate-400">
                  {{ group.weekday }}
                </span>
                <span
                  class="rounded-full bg-slate-100 px-1.5 text-[10.5px] font-semibold text-slate-500"
                >
                  {{ group.items.length }}
                </span>
                <span v-if="group.versions.shown.length" class="flex items-center gap-1">
                  <span
                    v-for="version in group.versions.shown"
                    :key="version"
                    class="rounded-md border border-primary/15 bg-primary/5 px-1.5 font-mono text-[10.5px] font-semibold text-primary"
                  >
                    v{{ version }}
                  </span>
                  <span
                    v-if="group.versions.more > 0"
                    class="rounded-md bg-slate-100 px-1.5 font-mono text-[10.5px] text-slate-500"
                  >
                    +{{ group.versions.more }}
                  </span>
                </span>
                <span
                  v-if="isCollapsed(group.key)"
                  class="ml-auto truncate text-[11px] text-slate-400"
                >
                  最近 {{ eventTone(group.items[0]).label }}
                </span>
              </button>

              <!-- 每组一条独立时间线：收起后不留断头竖线 -->
              <ol
                v-show="!isCollapsed(group.key)"
                class="relative ml-1.5 space-y-4 border-l border-slate-200 py-2 pl-5"
              >
                <li
                  v-for="entry in group.items"
                  :key="`${entry.kind}-${entry.id}`"
                  class="relative"
                >
                <span
                  class="absolute -left-[23px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white"
                  :class="eventTone(entry).dot"
                ></span>

                <div class="flex flex-wrap items-baseline gap-2">
                  <p class="text-sm font-semibold text-slate-800">{{ eventTone(entry).label }}</p>
                  <p class="text-[11px] text-slate-400">
                    {{ formatTime(entry.at) }}
                    <span
                      v-if="entry.kind === 'request' && entry.reviewed_at && entry.requested_at"
                      class="text-slate-300"
                    >（{{ formatTime(entry.requested_at) }} 提交）</span>
                  </p>
                </div>

                <p class="mt-0.5 text-xs text-slate-500">
                  {{ entry.actor_name || '系统' }}
                  <template v-if="entry.version_number"> · 版本 v{{ entry.version_number }}</template>
                  <template v-if="entry.affected_user_count !== null && entry.affected_user_count !== undefined">
                    · 影响 {{ entry.affected_user_count }} 人
                  </template>
                  <template v-if="entry.notified !== null && entry.notified !== undefined">
                    · {{ entry.notified ? '已发站内信' : '未通知用户' }}
                  </template>
                </p>

                <p
                  v-if="entry.reason"
                  class="mt-1 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs leading-relaxed text-slate-600"
                >
                  {{ entry.reason }}
                </p>
                <p
                  v-if="entry.comment"
                  class="mt-1 rounded-md border border-primary/10 bg-primary/5 px-2.5 py-1.5 text-xs leading-relaxed text-primary-dark"
                >
                  申请理由：{{ entry.comment }}
                </p>
                <p
                  v-if="entry.review_comment"
                  class="mt-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs leading-relaxed text-slate-600"
                >
                  审核意见：{{ entry.review_comment }}
                </p>

                <button
                  v-if="entry.snapshot"
                  class="mt-1 text-[11px] font-medium text-primary hover:underline"
                  @click="toggle(`${entry.kind}-${entry.id}`)"
                >
                  {{ expanded === `${entry.kind}-${entry.id}` ? '收起内容快照' : '查看内容快照' }}
                </button>

                <div
                  v-if="entry.snapshot && expanded === `${entry.kind}-${entry.id}`"
                  class="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
                >
                  <p class="text-[11px] font-semibold text-slate-600">
                    元数据：{{ entry.snapshot.agent?.display_name }}（{{ entry.snapshot.agent?.name }}）
                  </p>
                  <p v-if="entry.snapshot.version" class="text-[11px] text-slate-600">
                    版本 v{{ entry.snapshot.version.version_number }} · 模型
                    {{ entry.snapshot.version.model_name || '—' }}
                  </p>
                  <p
                    v-if="entry.snapshot.version"
                    class="text-[11px] leading-relaxed text-slate-500"
                  >
                    工具：{{ formatToolNames(entry.snapshot.version.tools) || '未绑定工具' }}
                  </p>
                  <pre
                    v-if="entry.snapshot.version?.system_prompt"
                    class="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-white p-2 font-mono text-[11px] leading-relaxed text-slate-600"
                    >{{ entry.snapshot.version.system_prompt }}</pre
                  >
                </div>
                </li>
              </ol>
            </section>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
