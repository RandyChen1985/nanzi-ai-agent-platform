<template>
  <div class="flex h-full min-h-0 flex-col">
    <!-- 品牌行：保留 logo 让用户知道自己在哪个平台，但不再放「返回首页」以外的入口 -->
    <div
      class="flex h-12 flex-shrink-0 items-center"
      :class="collapsed ? 'justify-center px-1' : 'gap-2.5 px-3.5'"
    >
      <!-- 收起态：logo 本身当展开按钮，否则收起来就没有回去的入口了 -->
      <button
        v-if="collapsed"
        type="button"
        class="flex h-9 w-9 items-center justify-center rounded-lg transition-colors hover:bg-gray-100 dark:hover:bg-white/[0.06]"
        title="展开侧栏"
        aria-label="展开侧栏"
        @click="emit('toggle-collapse')"
      >
        <img
          :src="branding.icon_url"
          alt="Logo"
          class="h-[26px] w-[26px] rounded-lg object-cover ring-1 ring-black/5"
        />
      </button>
      <img
        v-else
        :src="branding.icon_url"
        alt="Logo"
        class="h-[26px] w-[26px] flex-shrink-0 rounded-lg object-cover ring-1 ring-black/5"
      />
      <template v-if="!collapsed">
        <span
          class="min-w-0 flex-1 truncate text-[13px] font-semibold tracking-tight"
          :class="lightTheme ? 'text-gray-900' : 'text-white'"
          :title="branding.product_name"
        >{{ branding.product_name }}</span>
        <button
          type="button"
          class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
          title="收起侧栏"
          aria-label="收起侧栏"
          @click="emit('toggle-collapse')"
        >
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </template>
    </div>

    <div ref="scrollRef" class="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3 custom-scrollbar" @scroll.passive="onScroll">
      <nav class="flex flex-col gap-0.5" aria-label="用户模式导航">
        <template v-for="item in menu" :key="item.key">
          <!-- 一级项（非分组） -->
          <button
            v-if="item.kind !== 'group'"
            type="button"
            class="flex h-10 w-full items-center rounded-[11px] text-left transition-colors"
            :class="[navItemClass(item, isActive(item.path)), collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5 text-[15px]']"
            :disabled="item.locked"
            :title="item.locked ? lockedHint(item.label) : item.label"
            @click="onItemClick(item)"
          >
            <MenuIcon :name="item.icon" class="h-5 w-5 flex-shrink-0" :class="item.locked ? 'opacity-40' : ''" />
            <span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
            <svg
              v-if="item.locked"
              class="h-3.5 w-3.5 flex-shrink-0 opacity-50"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
            >
              <rect x="4.5" y="10.5" width="15" height="9.5" rx="2.5" />
              <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
            </svg>
          </button>

          <!-- 分组：标题本身可展开，展开后显示子项 -->
          <template v-else>
            <button
              type="button"
              class="flex h-10 w-full items-center rounded-[11px] text-left transition-colors"
              :class="[navItemClass(item, isChildActive(item)), collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5 text-[15px]']"
              :aria-expanded="expanded[item.key] !== false"
              :title="collapsed ? item.label : undefined"
              @click="toggleGroup(item)"
            >
              <MenuIcon :name="item.icon" class="h-5 w-5 flex-shrink-0" />
              <span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
              <svg
                v-if="!collapsed"
                class="h-4 w-4 flex-shrink-0 text-gray-400 transition-transform"
                :class="expanded[item.key] === false ? '-rotate-90' : ''"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" />
              </svg>
            </button>

            <div v-show="!collapsed && expanded[item.key] !== false" class="flex flex-col gap-0.5 pb-1 pl-[30px]">
              <button
                v-for="child in item.children"
                :key="child.key"
                type="button"
                class="flex h-[34px] w-full items-center gap-2 rounded-[9px] px-2 text-left text-sm transition-colors"
                :class="navSubClass(isActive(child.path), child.locked)"
                :disabled="child.locked"
                @click="onChildClick(child)"
              >
                <MenuIcon
                  v-if="child.icon"
                  :name="child.icon"
                  class="h-[15px] w-[15px] flex-shrink-0 opacity-85"
                />
                <span class="truncate">{{ child.label }}</span>
              </button>
              <!-- 「更多」末尾的快捷切换：个人中心是正式入口，这里只是少点一次 -->
              <button
                v-if="item.key === 'more'"
                type="button"
                class="mt-1 flex h-[34px] w-full items-center gap-2 rounded-[9px] px-2 text-left text-sm text-primary transition-colors hover:bg-primary/5"
                @click="emit('switch-mode', 'pro')"
              >
                <svg class="h-[15px] w-[15px] flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M8 7h8l-3-3M16 17H8l3 3" />
                </svg>
                <span class="truncate">切换到专业模式</span>
              </button>
            </div>
          </template>
        </template>
      </nav>

      <!-- 任务（历史会话）：收起侧栏时空间不够，整块隐藏 -->
      <div v-if="!collapsed" class="mt-3">
        <div class="flex items-center justify-between gap-2 px-2.5 py-1.5">
          <span v-if="!taskSearchOpen" class="text-xs text-gray-400">任务（{{ taskTotal }}）</span>
          <label v-else class="relative flex min-w-0 flex-1 items-center">
            <svg class="pointer-events-none absolute left-1.5 h-3.5 w-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
              <circle cx="11" cy="11" r="7" />
              <path d="M16.5 16.5L21 21" stroke-linecap="round" />
            </svg>
            <input
              ref="taskSearchRef"
              v-model="taskKeyword"
              type="search"
              placeholder="搜索任务"
              class="w-full rounded-md border border-gray-200 bg-white py-1 pl-6 pr-1 text-xs outline-none focus:border-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
              @input="onTaskSearchInput"
              @keydown.esc="closeTaskSearch"
            />
          </label>
          <button
            type="button"
            class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
            :title="taskSearchOpen ? '关闭搜索' : '搜索任务'"
            :aria-label="taskSearchOpen ? '关闭搜索' : '搜索任务'"
            @click="toggleTaskSearch"
          >
            <svg v-if="!taskSearchOpen" class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
              <circle cx="11" cy="11" r="7" />
              <path d="M16.5 16.5L21 21" stroke-linecap="round" />
            </svg>
            <svg v-else class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
            </svg>
          </button>
        </div>

        <div v-if="loadingTasks" class="space-y-1.5 px-1">
          <div
            v-for="index in 3"
            :key="index"
            class="h-8 animate-pulse rounded-lg"
            :class="lightTheme ? 'bg-gray-100' : 'bg-white/[0.06]'"
          />
        </div>

        <ul v-else-if="tasks.length" class="flex flex-col gap-0.5">
          <li v-for="item in tasks" :key="item.conversation_id || item.trace_id">
            <button
              type="button"
              class="group flex h-9 w-full items-center gap-2 rounded-[9px] px-2.5 text-left text-sm transition-colors"
              :class="
                isConversationActive(item)
                  ? 'bg-gray-200/80 font-semibold dark:bg-gray-700/70'
                  : lightTheme
                    ? 'text-gray-700 hover:bg-gray-100'
                    : 'text-slate-200 hover:bg-white/[0.06]'
              "
              :title="item.query || '未命名对话'"
              @click="emit('open-conversation', item)"
            >
              <span class="min-w-0 flex-1 truncate">{{ item.query || "未命名对话" }}</span>
              <span class="flex-shrink-0 text-xs text-gray-400">{{ formatDay(item.created_at) }}</span>
            </button>
          </li>
        </ul>

        <p v-else class="px-2.5 py-1.5 text-xs text-gray-400">
          {{ taskKeyword.trim() ? "没有匹配的任务" : "还没有任务，点「新建任务」开始。" }}
        </p>

        <!-- 滚到底部自动拉下一页，不再跳去聊天页看完整历史 -->
        <p v-if="loadingMore" class="px-2.5 py-2 text-center text-xs text-gray-400">正在加载…</p>
        <p
          v-else-if="tasks.length && !hasMoreTasks"
          class="px-2.5 py-2 text-center text-xs text-gray-300 dark:text-gray-600"
        >已加载全部会话</p>
      </div>
    </div>

    <!-- 用户卡：左半是个人信息，右半是原来顶栏的通知 / 主题 / 退出 -->
    <div
      class="flex flex-shrink-0 items-center gap-1.5 border-t p-2"
      :class="lightTheme ? 'border-gray-200/80 bg-white' : 'border-white/[0.06] bg-sidebar'"
    >
      <button
        type="button"
        class="flex min-w-0 items-center rounded-lg p-1 text-left transition-colors"
        :class="[
          collapsed ? 'flex-none justify-center' : 'flex-1 gap-2',
          lightTheme ? 'hover:bg-gray-100/80' : 'hover:bg-white/[0.06]',
        ]"
        :title="collapsed ? displayName : '查看个人信息'"
        @click="emit('open-personal')"
      >
        <img
          v-if="avatarUrl"
          :src="avatarUrl"
          alt="我的头像"
          class="h-8 w-8 flex-shrink-0 rounded-full object-cover ring-1 ring-black/5"
        />
        <div
          v-else
          class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-white"
          :style="{ background: 'linear-gradient(140deg, #4f8dff, #1b4fd8)' }"
        >{{ initial }}</div>

        <div v-if="!collapsed" class="min-w-0 flex-1">
          <p
            class="truncate text-[13px] font-semibold leading-tight"
            :class="lightTheme ? 'text-gray-900' : 'text-slate-200'"
          >{{ displayName }}</p>
          <p class="mt-0.5 truncate text-[11px] leading-none text-gray-400">{{ accountLabel }}</p>
        </div>
      </button>

      <template v-if="!collapsed">
      <!-- 站内通知：面板向上展开，避免被视口底部切掉 -->
      <PortalNotificationBell panel-placement="up" class="flex-shrink-0" />

      <button
        type="button"
        class="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
        :title="lightTheme ? '切换到深色' : '切换到浅色'"
        aria-label="切换界面主题"
        @click="emit('toggle-theme')"
      >
        <svg v-if="lightTheme" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <path stroke-linecap="round" stroke-linejoin="round" d="M20.5 14.3A8.5 8.5 0 0 1 9.7 3.5a8.5 8.5 0 1 0 10.8 10.8z" />
        </svg>
        <svg v-else class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <circle cx="12" cy="12" r="4" />
          <path stroke-linecap="round" d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4" />
        </svg>
      </button>

      <button
        type="button"
        class="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
        title="退出登录"
        aria-label="退出登录"
        @click="emit('logout')"
      >
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H15" />
          <path stroke-linecap="round" stroke-linejoin="round" d="M10 8l4 4-4 4M14 12H3.5" />
        </svg>
      </button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue"
import { useRoute } from "vue-router"
import axios from "@/utils/axios"
import MenuIcon from "@/components/menu/MenuIcon.vue"
import PortalNotificationBell from "@/components/PortalNotificationBell.vue"
import { useBranding } from "@/composables/useBranding"
import {
  USER_MODE_TASK_PAGE_SIZE,
  resolveUserModeMenu,
  type ResolvedUserModeChild,
  type ResolvedUserModeItem,
} from "@/constants/userModeMenu"

const props = defineProps<{
  lightTheme: boolean
  /** 当前用户 id，用于任务列表高亮当前会话 */
  userId: string
  userName: string
  realName: string
  avatarUrl: string
  role: string
  /** 当前用户的菜单权限码（permissions.menus） */
  menus: string[]
  /** 是否收起成图标条 */
  collapsed?: boolean
  /** 当前打开的会话 id（来自路由 query） */
  activeConversationId?: string
  mobile?: boolean
}>()

const emit = defineEmits<{
  (e: "navigate", path: string): void
  (e: "new-chat"): void
  (e: "open-conversation", item: ChatHistoryItem): void
  (e: "open-personal"): void
  (e: "switch-mode", mode: "user" | "pro"): void
  (e: "toggle-theme"): void
  (e: "logout"): void
  (e: "toggle-collapse"): void
  (e: "toast", message: string): void
}>()

interface ChatHistoryItem {
  conversation_id?: string | null
  trace_id?: string
  query?: string | null
  created_at?: string
}

const route = useRoute()
const { branding } = useBranding()

const menu = computed<ResolvedUserModeItem[]>(() =>
  resolveUserModeMenu({
    menus: props.menus || [],
    isAdmin: String(props.role || "").toLowerCase() === "admin",
  }),
)

/** 展开状态：默认「专家·技能·连接器」和「更多」都收起，只展开当前所在的那组 */
const expanded = reactive<Record<string, boolean>>({ experts: false, more: false })

/**
 * 路由变化时，只让「当前所在的那一组」保持展开，其余收起。
 *
 * 之前只写 `expanded[key] = true`、从不置回 false：点开过「更多」之后，
 * 再去点定时任务/知识库，那一组会一直挂在下面，看起来像「点别的菜单把更多也展开了」。
 * 手动点父项展开不受这里影响——那条路径不改路由。
 */
watch(
  () => route.path,
  (path) => {
    for (const item of menu.value) {
      if (item.kind !== "group" || !item.children) continue
      const containsCurrent = item.children.some((child) => pathMatches(path, child.path))
      expanded[item.key] = containsCurrent
    }
  },
  { immediate: true },
)

/**
 * 当前路由是否落在某个菜单路径上。
 *
 * 「系统概览」指向 /dashboard 本身，必须精确匹配：按前缀匹配的话，所有 /dashboard/*
 * 都算命中它——既会让它和当前页同时高亮，也会让「更多」这一组被判定为「包含当前路由」
 * 而永远展开。高亮与分组展开共用这一个判断，避免两处各写一份再跑偏。
 */
function pathMatches(current: string, target?: string): boolean {
  if (!target) return false
  if (target === "/dashboard" || target === "/dashboard/") return current === "/dashboard"
  return current === target || current.startsWith(`${target}/`)
}

const isActive = (path?: string): boolean => pathMatches(route.path, path)

const isChildActive = (item: ResolvedUserModeItem): boolean =>
  (item.children || []).some((child) => isActive(child.path))

const toggleGroup = (item: ResolvedUserModeItem) => {
  // 收起成图标条时没有空间展示子项：点一下直接进第一个可用的子项
  if (props.collapsed) {
    const first = (item.children || []).find((child) => !child.locked)
    if (first) emit("navigate", first.path)
    return
  }
  const isOpen = expanded[item.key] !== false
  // 「专家·技能·连接器」是「点即进入」的组：只要当前不在这组里，点一下就展开并进
  // 第一个子项（专家），而不是先展开、再让用户点一次子项。
  if (item.expandOpensFirst && !isChildActive(item)) {
    expanded[item.key] = true
    const first = (item.children || []).find((child) => !child.locked)
    if (first) emit("navigate", first.path)
    return
  }
  expanded[item.key] = !isOpen
}

const navItemClass = (item: ResolvedUserModeItem, active: boolean): string => {
  if (item.locked) return props.lightTheme ? "text-gray-400 cursor-not-allowed" : "text-slate-500 cursor-not-allowed"
  if (active) {
    return props.lightTheme
      ? "bg-gray-200/80 font-semibold text-gray-900"
      : "bg-white/10 font-semibold text-white"
  }
  return props.lightTheme
    ? "text-gray-800 hover:bg-gray-100"
    : "text-slate-200 hover:bg-white/[0.06]"
}

const navSubClass = (active: boolean, locked: boolean): string => {
  if (locked) return props.lightTheme ? "text-gray-300 cursor-not-allowed" : "text-slate-600 cursor-not-allowed"
  if (active) {
    return props.lightTheme
      ? "bg-gray-200/80 font-semibold text-gray-900 shadow-[inset_2px_0_0_0_#1677ff]"
      : "bg-white/10 font-semibold text-white"
  }
  return props.lightTheme ? "text-gray-600 hover:bg-gray-100" : "text-slate-300 hover:bg-white/[0.06]"
}

const lockedHint = (label: string) => `当前账号没有「${label}」权限，请联系管理员开通`

const onItemClick = (item: ResolvedUserModeItem) => {
  if (item.locked) {
    emit("toast", lockedHint(item.label))
    return
  }
  if (item.kind === "new-chat") {
    emit("new-chat")
    return
  }
  if (item.path) emit("navigate", item.path)
}

const onChildClick = (child: ResolvedUserModeChild) => {
  if (child.locked) {
    emit("toast", lockedHint(child.label))
    return
  }
  emit("navigate", child.path)
}

/* ---------------- 任务列表 ---------------- */
const tasks = ref<ChatHistoryItem[]>([])
const taskTotal = ref(0)
const loadingTasks = ref(false)
const loadingMore = ref(false)
const hasMoreTasks = ref(true)
const tasksPage = ref(1)
const scrollRef = ref<HTMLElement | null>(null)

const taskKeyword = ref("")
const taskSearchOpen = ref(false)
const taskSearchRef = ref<HTMLInputElement | null>(null)

const fetchTaskPage = async (page: number) => {
  const params: Record<string, any> = {
    page,
    page_size: USER_MODE_TASK_PAGE_SIZE,
    group_by_conversation: true,
  }
  const keyword = taskKeyword.value.trim()
  if (keyword) params.keyword = keyword
  const res = await axios.get("/api/v1/chat/history", { params })
  return res?.data?.data || {}
}

/** 搜索输入：300ms 防抖后从第一页重新拉（沿用同一套分页与滚动加载） */
let taskSearchTimer: ReturnType<typeof setTimeout> | null = null
const onTaskSearchInput = () => {
  if (taskSearchTimer) clearTimeout(taskSearchTimer)
  taskSearchTimer = setTimeout(() => {
    void loadTasks()
  }, 300)
}

const toggleTaskSearch = () => {
  if (taskSearchOpen.value) {
    closeTaskSearch()
    return
  }
  taskSearchOpen.value = true
  void nextTick(() => taskSearchRef.value?.focus())
}

const closeTaskSearch = () => {
  taskSearchOpen.value = false
  if (taskSearchTimer) clearTimeout(taskSearchTimer)
  if (taskKeyword.value) {
    taskKeyword.value = ""
    void loadTasks()
  }
}

/** 重新加载第一页（切页 / 上传完成 / 进入聊天页时调用） */
const loadTasks = async () => {
  loadingTasks.value = true
  tasksPage.value = 1
  try {
    const data = await fetchTaskPage(1)
    const items = Array.isArray(data.items) ? data.items : []
    tasks.value = items
    taskTotal.value = Number(data.total ?? items.length) || items.length
    // 不足一页 = 没有更多了
    hasMoreTasks.value = items.length >= USER_MODE_TASK_PAGE_SIZE
    tasksPage.value = 2
  } catch {
    // 历史接口失败不该拖垮侧栏：退化成空列表，用户仍能点「新建任务」
    tasks.value = []
    taskTotal.value = 0
    hasMoreTasks.value = false
  } finally {
    loadingTasks.value = false
  }
}

/** 追加下一页 */
const loadMoreTasks = async () => {
  if (loadingMore.value || loadingTasks.value || !hasMoreTasks.value) return
  loadingMore.value = true
  try {
    const data = await fetchTaskPage(tasksPage.value)
    const items = Array.isArray(data.items) ? data.items : []
    const seen = new Set(tasks.value.map((item) => item.conversation_id || item.trace_id))
    const fresh = items.filter((item) => !seen.has(item.conversation_id || item.trace_id))
    tasks.value = [...tasks.value, ...fresh]
    taskTotal.value = Number(data.total ?? taskTotal.value) || taskTotal.value
    hasMoreTasks.value = items.length >= USER_MODE_TASK_PAGE_SIZE
    tasksPage.value += 1
  } catch {
    // 失败就停在当前列表，不反复重试；用户滚动一次会再触发
    hasMoreTasks.value = false
  } finally {
    loadingMore.value = false
  }
}

/**
 * 滚到底部自动加载下一页。
 *
 * 任务列表和菜单共用同一个滚动容器，所以监听的是整个侧栏滚动区：
 * 距离底部 48px 内就补一页，够用户在列表末尾继续滑下去。
 */
const onScroll = () => {
  const el = scrollRef.value
  if (!el) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) void loadMoreTasks()
}

const formatDay = (value?: string): string => {
  if (!value) return ""
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ""
  return `${parsed.getMonth() + 1}月${parsed.getDate()}日`
}

const isConversationActive = (item: ChatHistoryItem): boolean =>
  Boolean(props.activeConversationId && item.conversation_id === props.activeConversationId)

const displayName = computed(() => props.realName || props.userName || "未登录")
const initial = computed(() => Array.from(props.realName || props.userName || "U").slice(-1).join(""))
const accountLabel = computed(() => (props.userName ? `@${props.userName}` : ""))

onMounted(loadTasks)
// 从聊天页回到列表时刷新一次：刚发起的会话可能才刚写进历史
watch(() => route.path, (path) => {
  if (path === "/dashboard/chat") void loadTasks()
})
</script>
