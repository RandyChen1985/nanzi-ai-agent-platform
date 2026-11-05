<template>
  <transition name="account-menu">
    <div
      v-if="visible"
      class="account-menu-layer"
      :class="mobile ? 'fixed inset-x-0 bottom-0 z-[70]' : 'absolute bottom-[76px] left-2 z-[60] w-[352px]'"
    >
      <!-- 移动端：底部抽屉 + 遮罩；点遮罩只关菜单，不关侧栏抽屉 -->
      <div
        v-if="mobile"
        class="fixed inset-0 -z-10 bg-black/40 backdrop-blur-[1px]"
        @click="close"
      />
      <!-- 桌面：透明捕获层。聊天区是 iframe，点它时 click 落在 iframe 自己的文档里，
           不会冒泡到父文档，只靠 document 监听在聊天区点不出来；铺一层透明层把菜单外
           任意一点（含 iframe 区域）都变成「关闭」，视觉上无变化。 -->
      <div
        v-else
        class="fixed inset-0 -z-10 bg-transparent"
        aria-hidden="true"
        @click="close"
      />

      <div
        ref="rootRef"
        role="menu"
        aria-label="账户菜单"
        class="account-menu-card flex flex-col"
        :class="[
          lightTheme ? 'bg-white border-gray-200/80' : 'bg-[#171d27] border-white/10',
          mobile
            ? 'max-h-[86vh] overflow-y-auto rounded-t-2xl border-b-0 p-2 pb-[max(10px,env(safe-area-inset-bottom))]'
            : 'max-h-[calc(100vh-120px)] overflow-y-auto rounded-2xl border p-2',
        ]"
        @keydown.esc.stop.prevent="close"
      >
        <!-- 头部用户块：整行可点，进个人中心 -->
        <button
          type="button"
          role="menuitem"
          class="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors"
          :class="lightTheme ? 'hover:bg-gray-100' : 'hover:bg-white/[0.06]'"
          @click="go('/dashboard/personal')"
        >
          <img
            v-if="avatarUrl"
            :src="avatarUrl"
            alt="我的头像"
            class="h-10 w-10 flex-shrink-0 rounded-full object-cover ring-1 ring-black/5"
          />
          <div
            v-else
            class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full text-[13px] font-semibold text-white"
            :style="{ background: 'linear-gradient(140deg, #34d399, #059669)' }"
          >{{ initial }}</div>
          <div class="min-w-0 flex-1">
            <p class="truncate text-[13.5px] font-semibold leading-tight" :class="lightTheme ? 'text-gray-900' : 'text-slate-100'">
              {{ displayName }}
            </p>
            <p class="mt-0.5 truncate text-[11px] leading-none" :class="mutedClass">
              @{{ userName || "user" }} · {{ roleLabel }}
            </p>
          </div>
          <span class="flex flex-shrink-0 items-center gap-0.5 text-[10.5px]" :class="mutedClass">
            个人中心
            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </span>
        </button>

        <!-- 界面模式：两枚胶囊，选中态打勾；这是本菜单的核心，放第一屏 -->
        <div class="px-1 pb-1 pt-0.5">
          <div class="flex items-center justify-between px-1.5 pb-1.5">
            <span class="text-[10.5px] font-medium tracking-wide" :class="mutedClass">界面模式</span>
            <span
              class="rounded-full px-1.5 py-0.5 text-[9.5px] font-semibold"
              :class="lightTheme ? 'bg-primary/10 text-primary' : 'bg-primary/20 text-blue-300'"
            >一步切换</span>
          </div>
          <div class="grid grid-cols-2 gap-1.5">
            <button
              v-for="option in modeOptions"
              :key="option.value"
              type="button"
              role="menuitemradio"
              :aria-checked="mode === option.value"
              class="relative flex flex-col gap-0.5 rounded-[11px] border px-2.5 py-2 text-left transition-colors"
              :class="[
                mode === option.value
                  ? lightTheme
                    ? 'border-primary/40 bg-primary/[0.08]'
                    : 'border-primary/45 bg-primary/15'
                  : lightTheme
                    ? 'border-gray-200 hover:bg-gray-50'
                    : 'border-white/10 hover:bg-white/[0.05]',
              ]"
              @click="emit('switch-mode', option.value)"
            >
              <span class="flex items-center gap-1.5 text-[12.5px] font-semibold" :class="lightTheme ? 'text-gray-900' : 'text-slate-100'">
                <svg class="h-[15px] w-[15px] flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                  <path v-for="(d, index) in option.icon" :key="index" :d="d" />
                </svg>
                {{ option.label }}
              </span>
              <span class="text-[10.5px] leading-tight" :class="mutedClass">{{ option.desc }}</span>
              <span
                v-if="mode === option.value"
                class="absolute right-1.5 top-1.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-primary text-white"
                aria-hidden="true"
              >
                <svg class="h-2 w-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M20 6L9 17l-5-5" />
                </svg>
              </span>
            </button>
          </div>
        </div>

        <div class="my-1.5 h-px" :class="dividerClass" role="separator" />

        <div role="group" aria-label="账户">
          <button type="button" role="menuitem" class="account-menu-item" :class="itemClass" @click="go('/dashboard/personal')">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><circle cx="12" cy="8" r="4" /><path d="M4 21c0-3.6 3.6-6 8-6s8 2.4 8 6" /></svg>
            个人中心
            <span class="ml-auto text-[11px]" :class="mutedClass">资料 · 安全 · 偏好</span>
            <Chevron />
          </button>
          <button type="button" role="menuitem" class="account-menu-item" :class="itemClass" @click="go('/dashboard/personal?tab=permissions')">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z" /><path d="M9 12l2 2 4-4" /></svg>
            我的权限
            <span class="ml-auto text-[11px]" :class="mutedClass">菜单与功能</span>
            <Chevron />
          </button>
          <button type="button" role="menuitem" class="account-menu-item" :class="itemClass" @click="go('/dashboard/personal?tab=tokens')">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><path d="M4 19V9M10 19V5M16 19v-7M22 19H2" /></svg>
            Token 用量
            <span class="ml-auto text-[11px]" :class="mutedClass">本月消耗</span>
            <Chevron />
          </button>
        </div>

        <div class="my-1.5 h-px" :class="dividerClass" role="separator" />

        <div role="group" aria-label="设置">
          <button type="button" role="menuitem" class="account-menu-item" :class="itemClass" @click="go('/dashboard/personal?tab=notifications')">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><path d="M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8" /><path d="M13.7 21a2 2 0 01-3.4 0" /></svg>
            通知设置
            <span class="ml-auto text-[11px]" :class="mutedClass">站内消息</span>
            <Chevron />
          </button>

          <!-- 外观：直接在菜单里切，不跳页 -->
          <div class="account-menu-item" :class="itemClass">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 000 18c1.5 0 2.5-1 2.5-2.2 0-1.2-.9-1.9-.9-2.8 0-.9.7-1.5 1.6-1.5H17a4 4 0 004-4c0-4.4-4-7.5-9-7.5z" /></svg>
            外观
            <span class="ml-auto inline-flex items-center gap-0.5 rounded-lg p-0.5" :class="pillClass">
              <button
                v-for="option in themeOptions"
                :key="option.value"
                type="button"
                role="menuitemradio"
                :aria-checked="theme === option.value"
                class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium transition-colors"
                :class="theme === option.value
                  ? (lightTheme ? 'bg-white text-gray-900 shadow-sm' : 'bg-white/15 text-white')
                  : mutedClass"
                @click.stop="emit('set-theme', option.value)"
              >
                <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path v-for="(d, index) in option.icon" :key="index" :d="d" />
                </svg>
                {{ option.label }}
              </button>
            </span>
          </div>

          <button
            type="button"
            role="menuitem"
            class="account-menu-item"
            :class="itemClass"
            :aria-expanded="aboutOpen"
            @click="aboutOpen = !aboutOpen"
          >
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><circle cx="12" cy="7.8" r="0.7" fill="currentColor" stroke="none" /></svg>
            关于
            <span class="ml-auto text-[11px]" :class="mutedClass">{{ version }}</span>
            <svg
              class="ml-1 h-3 w-3 flex-shrink-0 transition-transform"
              :class="[mutedClass, aboutOpen ? 'rotate-180' : '']"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            ><path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" /></svg>
          </button>

          <div
            v-if="aboutOpen"
            class="mx-1 mb-1 rounded-lg px-2.5 py-2 text-[11px] leading-relaxed"
            :class="lightTheme ? 'bg-gray-50 text-gray-500' : 'bg-white/[0.04] text-slate-400'"
          >
            <p>当前版本 {{ version }}</p>
            <a
              v-if="repoUrl"
              :href="repoUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="mt-0.5 inline-block font-medium underline underline-offset-2"
              :class="lightTheme ? 'text-primary' : 'text-blue-300'"
            >代码仓库</a>
            <p class="mt-0.5 opacity-80">更新日志：后续版本提供。</p>
          </div>
        </div>

        <div class="my-1.5 h-px" :class="dividerClass" role="separator" />

        <button
          type="button"
          role="menuitem"
          class="account-menu-item"
          :class="lightTheme
            ? 'text-rose-600 hover:bg-rose-50'
            : 'text-rose-400 hover:bg-rose-500/10'"
          @click="emit('logout')"
        >
          <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 4h3.5A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5H15" /><path d="M10 8l4 4-4 4M14 12H3.5" /></svg>
          退出登录
          <span class="ml-auto text-[11px] opacity-70">需要重新登录</span>
        </button>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"

/**
 * 账户菜单：专业模式与用户模式共用同一份内容。
 *
 * 两套侧栏只是触发位置不同（左下角头像），菜单本身必须完全一致——
 * 否则用户切换模式后，同一个头像点出来的东西变了，心智会断。
 * 模式胶囊的选中态由 `mode` 决定，切完由父级重新渲染。
 */
const props = withDefaults(defineProps<{
  visible: boolean
  lightTheme: boolean
  theme: "light" | "dark"
  mode: "user" | "pro"
  userName: string
  realName: string
  avatarUrl: string
  role: string
  version: string
  repoUrl?: string
  mobile?: boolean
}>(), {
  repoUrl: "",
  mobile: false,
})

const emit = defineEmits<{
  (e: "close"): void
  (e: "switch-mode", mode: "user" | "pro"): void
  (e: "set-theme", theme: "light" | "dark"): void
  (e: "navigate", path: string): void
  (e: "logout"): void
}>()

const rootRef = ref<HTMLElement | null>(null)
const aboutOpen = ref(false)
let lastFocused: HTMLElement | null = null

const displayName = computed(() => props.realName || props.userName || "未登录")
const initial = computed(() => Array.from(displayName.value).slice(-1).join(""))
const roleLabel = computed(() =>
  String(props.role || "").toLowerCase() === "admin" ? "管理员" : "普通用户",
)

const modeOptions: Array<{ value: "user" | "pro"; label: string; desc: string; icon: string[] }> = [
  {
    value: "user",
    label: "用户模式",
    desc: "只留任务与知识库",
    icon: [
      "M4 5.5h6v6H4z",
      "M14 5.5h6v6h-6z",
      "M4 14.5h6v6H4z",
      "M14 14.5h6v6h-6z",
    ],
  },
  {
    value: "pro",
    label: "专业模式",
    desc: "完整工作台与权限",
    icon: ["M3 5h18v14H3z", "M3 9.5h18", "M9 9.5V19"],
  },
]

const themeOptions: Array<{ value: "light" | "dark"; label: string; icon: string[] }> = [
  {
    value: "light",
    label: "浅色",
    icon: ["M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4", "M12 8a4 4 0 100 8 4 4 0 000-8z"],
  },
  {
    value: "dark",
    label: "深色",
    icon: ["M20.5 14.3A8.5 8.5 0 019.7 3.5a8.5 8.5 0 1010.8 10.8z"],
  },
]

const mutedClass = computed(() => (props.lightTheme ? "text-gray-400" : "text-slate-400"))
const iconClass = computed(() => (props.lightTheme ? "text-gray-500" : "text-slate-400"))
const dividerClass = computed(() => (props.lightTheme ? "bg-gray-100" : "bg-white/[0.07]"))
const pillClass = computed(() => (props.lightTheme ? "bg-gray-100" : "bg-white/[0.06]"))
const itemClass = computed(() =>
  props.lightTheme
    ? "text-gray-700 hover:bg-gray-100"
    : "text-slate-200 hover:bg-white/[0.06]",
)

const close = () => emit("close")
const go = (path: string) => emit("navigate", path)

// 箭头图标抽成函数式组件，避免每个菜单项重复写一段 SVG
const Chevron = () => h("svg", {
  class: "ml-0.5 h-3 w-3 flex-shrink-0",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  "stroke-width": 2,
}, [h("path", { d: "M9 5l7 7-7 7", "stroke-linecap": "round", "stroke-linejoin": "round" })])

const onDocumentClick = (event: MouseEvent) => {
  if (!props.visible) return
  const target = event.target as HTMLElement | null
  if (!target) return
  if (rootRef.value?.contains(target)) return
  // 触发按钮自己负责 toggle，这里跳过，否则「点开→document 又关掉」会闪
  if (target.closest("[data-account-trigger]")) return
  close()
}

watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      lastFocused = (document.activeElement as HTMLElement | null) ?? null
      aboutOpen.value = false
      await nextTick()
      rootRef.value
        ?.querySelector<HTMLElement>('[role="menuitem"], [role="menuitemradio"]')
        ?.focus()
    } else {
      // 关闭后把焦点还给头像，键盘用户不会掉到页面顶部
      lastFocused?.focus?.()
      lastFocused = null
    }
  },
)

const onDocumentKeydown = (event: KeyboardEvent) => {
  if (event.key === "Escape" && props.visible) close()
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick)
  document.addEventListener("keydown", onDocumentKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick)
  document.removeEventListener("keydown", onDocumentKeydown)
})
</script>

<style scoped>
/* 菜单行：图标 + 文字 + 右侧提示/箭头。模板里靠这个类做行布局，
   缺了它按钮会退化成 inline，图标与文案会各占一行。 */
.account-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  height: 38px;
  padding: 0 10px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  transition: background-color 0.12s ease;
}

.account-menu-item:focus-visible {
  outline: 2px solid rgba(22, 119, 255, 0.55);
  outline-offset: -2px;
}

.account-menu-layer {
  transform-origin: bottom left;
}

.account-menu-enter-active,
.account-menu-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.account-menu-enter-from,
.account-menu-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.98);
}

.account-menu-card {
  box-shadow: 0 18px 44px -12px rgba(15, 23, 42, 0.28), 0 2px 8px rgba(15, 23, 42, 0.08);
}

@media (max-width: 1023px) {
  .account-menu-layer {
    transform-origin: bottom center;
  }
}
</style>
