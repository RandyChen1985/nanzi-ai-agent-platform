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

        <!-- 界面模式：两枚胶囊常显。被角色禁用的那枚置灰不可点——直接藏起来会让用户
             以为平台没有这个模式，也找不到该找谁。 -->
        <div class="px-1 pb-1 pt-0.5">
          <div class="flex items-center px-1.5 pb-1.5">
            <span class="text-[10.5px] font-medium tracking-wide" :class="mutedClass">界面模式</span>
          </div>
          <div class="grid grid-cols-2 gap-1.5">
            <button
              v-for="option in modeOptions"
              :key="option.value"
              type="button"
              role="menuitemradio"
              :aria-checked="mode === option.value"
              :aria-disabled="isModeAllowed(option.value) ? undefined : 'true'"
              :disabled="!isModeAllowed(option.value)"
              :title="isModeAllowed(option.value) ? undefined : modeBlockedReason"
              class="relative"
              :class="modeCardClass(option.value)"
              @click="emit('switch-mode', option.value)"
            >
              <span class="flex items-center gap-1.5 text-[12.5px] font-semibold" :class="lightTheme ? 'text-gray-900' : 'text-slate-100'">
                <svg class="h-[15px] w-[15px] flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                  <path v-for="(d, index) in option.icon" :key="index" :d="d" />
                </svg>
                {{ option.label }}
              </span>
              <span class="text-[10.5px] leading-tight" :class="mutedClass">{{ option.desc }}</span>
              <!-- 当前模式：打勾；被禁用：挂锁，鼠标悬停看原因 -->
              <span
                v-if="mode === option.value && isModeAllowed(option.value)"
                class="absolute right-1.5 top-1.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-primary text-white"
                aria-hidden="true"
              >
                <svg class="h-2 w-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M20 6L9 17l-5-5" />
                </svg>
              </span>
              <span
                v-else-if="!isModeAllowed(option.value)"
                class="absolute right-1.5 top-1.5 flex h-3.5 w-3.5 items-center justify-center"
                :class="lightTheme ? 'text-gray-400' : 'text-slate-500'"
                aria-hidden="true"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="4" y="10" width="16" height="10" rx="2" />
                  <path d="M8 10V7a4 4 0 018 0v3" />
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
          <button type="button" role="menuitem" class="account-menu-item" :class="itemClass" @click="go('/dashboard/personal?tab=secrets')">
            <svg class="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" :class="iconClass"><rect x="4" y="10.5" width="16" height="9.5" rx="2.2" /><path d="M8 10.5V7.2a4 4 0 018 0v3.3" /><path d="M12 14.4v2.2" /></svg>
            我的凭据
            <span class="ml-auto text-[11px]" :class="mutedClass">密钥与令牌</span>
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
            <span class="ml-auto flex items-center gap-1.5 text-[11px]" :class="mutedClass">
              <span v-if="hasPendingUpdate" class="update-badge-dot" :title="`有新版本 ${updateData?.latest || ''}`" />
              {{ version }}
            </span>
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
            <p class="flex items-center gap-1.5">
              <span>当前版本 {{ version }}</span>
              <!-- 版本检测：只有配置了仓库地址（白标未关闭版本入口）时才出现。
                   判定全在后端，这里只把结果翻译成一个点 + 一句话。 -->
              <span
                v-if="repoUrl && updateStatusText"
                class="ml-auto inline-flex flex-shrink-0 items-center gap-1"
                :class="updateStatusClass"
              >
                <svg
                  v-if="updateState === 'checking'"
                  class="h-2.5 w-2.5 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="3"
                  aria-hidden="true"
                ><circle cx="12" cy="12" r="9" stroke-opacity=".25" /><path d="M21 12a9 9 0 0 0-9-9" stroke-linecap="round" /></svg>
                <span v-else class="update-status-dot" aria-hidden="true" />
                {{ updateStatusText }}
              </span>
            </p>

            <!-- 有新版本：窄面板放不下完整 changelog，只给一句摘要 + 三个动作 -->
            <div
              v-if="updateState === 'outdated' && updateData"
              class="mt-1.5 rounded-md border px-2 py-1.5"
              :class="lightTheme ? 'border-amber-300/70 bg-amber-50' : 'border-amber-400/30 bg-amber-400/10'"
            >
              <p class="font-medium" :class="lightTheme ? 'text-amber-700' : 'text-amber-300'">
                {{ updateData.latest }}<span v-if="publishedDate"> · {{ publishedDate }} 发布</span>
              </p>
              <p
                v-if="updateNotes.length"
                class="mt-0.5 max-h-[34px] overflow-hidden"
                :class="lightTheme ? 'text-amber-800/80' : 'text-amber-100/70'"
              >{{ updateNotes.join(" · ") }}</p>
              <p v-if="updateData.has_migrations" class="mt-0.5" :class="lightTheme ? 'text-amber-700/90' : 'text-amber-200/70'">
                本次更新含数据库迁移，升级前请先执行迁移脚本
              </p>
              <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <button
                  v-if="updateData.body"
                  type="button"
                  class="font-medium underline underline-offset-2"
                  :class="lightTheme ? 'text-primary' : 'text-blue-300'"
                  @click="notesOpen = true"
                >查看更新内容</button>
                <!-- 正文没缓存下来（上游没填发行说明 / 老缓存）时退回新标签打开仓库 -->
                <a
                  v-else
                  :href="updateData.release_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="font-medium underline underline-offset-2"
                  :class="lightTheme ? 'text-primary' : 'text-blue-300'"
                >查看更新内容 ↗</a>
                <button type="button" class="update-link" @click="onDeferUpdate">稍后</button>
                <button type="button" class="update-link" :disabled="ignoringUpdate" @click="onIgnoreUpdate">忽略此版本</button>
              </div>
              <p v-if="ignoreError" class="mt-1" :class="lightTheme ? 'text-rose-600' : 'text-rose-300'">{{ ignoreError }}</p>
            </div>

            <!-- 开发版：本地没有可比的版本号，判不出「是不是最新」，但上游现在是多少
                 是明确的。给一块和「有新版本」同形态的提示，让使用者自己决定要不要拉。 -->
            <div
              v-else-if="updateUncomparable && updateData"
              class="mt-1.5 rounded-md border px-2 py-1.5"
              :class="lightTheme ? 'border-sky-300/70 bg-sky-50' : 'border-sky-400/25 bg-sky-400/10'"
            >
              <p class="font-medium" :class="lightTheme ? 'text-sky-700' : 'text-sky-300'">
                上游最新 {{ updateData.latest }}<span v-if="publishedDate"> · {{ publishedDate }} 发布</span>
              </p>
              <p class="mt-0.5" :class="lightTheme ? 'text-sky-800/80' : 'text-sky-100/70'">
                当前是开发版，判断不了是否落后，请自行确认
              </p>
              <p v-if="updateData.has_migrations" class="mt-0.5" :class="lightTheme ? 'text-sky-700/90' : 'text-sky-200/70'">
                该版本含数据库迁移，同步代码后请先执行迁移脚本
              </p>
              <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <button
                  v-if="updateData.body"
                  type="button"
                  class="font-medium underline underline-offset-2"
                  :class="lightTheme ? 'text-primary' : 'text-blue-300'"
                  @click="notesOpen = true"
                >查看更新内容</button>
                <a
                  v-else
                  :href="updateData.release_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="font-medium underline underline-offset-2"
                  :class="lightTheme ? 'text-primary' : 'text-blue-300'"
                >查看更新内容 ↗</a>
              </div>
            </div>

            <!-- 已忽略 / 已稍后：给条恢复入口，别让用户以为提示坏了 -->
            <p v-else-if="updateState === 'ignored' && updateData" class="mt-1">
              已忽略 <span :class="lightTheme ? 'text-gray-700' : 'text-slate-300'">{{ updateData.latest }}</span> 的提示
              · <button type="button" class="update-link" @click="onRestoreUpdate">恢复提示</button>
            </p>

            <!-- 检查按钮常驻：上面是「结果」，这里是「动作」，两者互不挤占。
                 检查中只置灰不移除，节点不消失位置就不会跳。 -->
            <div v-if="repoUrl" class="mt-1 flex items-center gap-2">
              <button
                type="button"
                class="update-link font-medium"
                :disabled="updateState === 'checking'"
                @click="onCheckUpdate"
              >{{ updateActionLabel }}</button>
              <span v-if="updateFailure" class="truncate" :class="lightTheme ? 'text-rose-600' : 'text-rose-300'">{{ updateFailure }}</span>
              <span v-else-if="throttleHint" class="truncate">{{ throttleHint }}</span>
            </div>

            <!-- 「代码仓库」入口已去掉：它与下面的「更新日志」指向同一个仓库，
                 两条并排是重复入口，只留更新日志（那边正好落在对应 tag 上）。 -->
            <p v-if="repoUrl" class="mt-0.5">
              <!-- 已是最新时，缓存的发行说明正是这个版本的内容，就地弹层看；
                   落后时这个入口指向历史版本，缓存里没有那份正文，保留外链。 -->
              <button
                v-if="changelogOpensNotes"
                type="button"
                class="font-medium underline underline-offset-2"
                :class="lightTheme ? 'text-primary' : 'text-blue-300'"
                @click="notesOpen = true"
              >更新日志：{{ changelogLabel }}</button>
              <a
                v-else
                :href="changelogUrl"
                target="_blank"
                rel="noopener noreferrer"
                class="font-medium underline underline-offset-2"
                :class="lightTheme ? 'text-primary' : 'text-blue-300'"
              >更新日志：{{ changelogLabel }} ↗</a>
            </p>
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
      <!-- 更新内容弹层：Teleport 到 body，不受菜单层级与裁剪影响 -->
      <UpdateNotesDialog
        :visible="notesOpen"
        :version="updateData?.latest || ''"
        :published-at="publishedDate"
        :has-migrations="Boolean(updateData?.has_migrations)"
        :body="updateData?.body || ''"
        :release-url="updateData?.release_url || ''"
        :github-url="updateData?.github_release_url || ''"
        :light-theme="lightTheme"
        @close="notesOpen = false"
      />
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import UpdateNotesDialog from "./UpdateNotesDialog.vue"
import {
  checkForUpdate,
  deferUpdateVersion,
  ensureUpdateChecked,
  ignoreUpdateVersion,
  normalizeVersion,
  restoreUpdatePrompt,
  useUpdateCheck,
} from "@/composables/useUpdateCheck"

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
  // 角色级黑名单：被禁用的模式进不来。默认两种都能用
  allowedModes?: Array<"user" | "pro">
  // 禁用来源角色名，用于说明「是谁固定的」
  blockedBy?: string[]
}>(), {
  repoUrl: "",
  mobile: false,
  allowedModes: () => ["user", "pro"],
  blockedBy: () => [],
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

/**
 * 「更新日志」指向 GitHub Releases。
 *
 * 版本号是四段（1.0.22.0），最后一段是构建号，而 Release tag 只到三段（1.0.22）——
 * 取错段会拼出 /releases/tag/1.0 这种必然 404 的地址。
 *
 * 开发版（dev）与残缺版本号没有对应的 tag，但不该因此把入口藏起来：落到 Releases
 * 列表页，想看日志的人照样能找到路。用 repoUrl 拼接而不是硬编码 GitHub 前缀，
 * 白标部署换了仓库地址也能跟着走。
 */
const changelogTag = computed(() => {
  const raw = String(props.version || "").trim().replace(/^v/i, "")
  if (!raw || /^dev/i.test(raw)) return ""
  const parts = raw.split(".")
  if (parts.length < 2) return ""
  const core = parts.length > 3 ? parts.slice(0, 3) : parts
  if (!core.every((part) => /^\d+$/.test(part))) return ""
  return core.join(".")
})
const changelogLabel = computed(() => (changelogTag.value ? `v${changelogTag.value}` : "查看全部版本"))
const changelogUrl = computed(() => {
  if (!props.repoUrl) return ""
  const base = props.repoUrl.replace(/\/+$/, "")
  return changelogTag.value ? `${base}/releases/tag/${changelogTag.value}` : `${base}/releases`
})

/**
 * 版本检测：当前版本与上游最新 tag。
 *
 * 判定与缓存都在后端（`useUpdateCheck` 只是取数 + 展示），这里不重写比较逻辑——
 * 四段版本与三段 tag 的补零比较一旦两处各写一份，就一定会有一天对不上。
 *
 * 只有「有新版且没被忽略/稍后」才渲染那块提示；其余状态都收成一行，不打扰。
 */
const {
  data: updateData,
  state: updateState,
  notes: updateNotes,
  failure: updateFailure,
  throttleHint,
  hasPendingUpdate,
  publishedDate,
} = useUpdateCheck()

const ignoringUpdate = ref(false)
const ignoreError = ref("")
/** 更新内容弹层是否打开（正文来自后端缓存的发行说明） */
const notesOpen = ref(false)

/** dev 构建没有可比版本号：不显示「已是最新」这种没有意义的结论。 */
const updateComparable = computed(() => normalizeVersion(props.version) !== "")

/** 开发版且已拿到上游版本：能说「最新是多少」，但判定不了「要不要更新」 */
const updateUncomparable = computed(
  () => !updateComparable.value && Boolean(updateData.value?.latest),
)

/**
 * 「更新日志」能否用弹层看。
 *
 * 后端只缓存了**最新那次**发行说明的正文。所以只有当上游最新 tag 与当前版本
 * 是同一个版本（即「已是最新」）时，缓存里的正文才是这个版本的内容；
 * 落后时这个入口指向的是历史版本，我们手上没有那份正文，只能回落到外链——
 * 宁可跳一次浏览器，也不能拿最新版的说明冒充旧版的。
 */
const changelogOpensNotes = computed(() => {
  if (!updateData.value?.body) return false
  const latest = normalizeVersion(updateData.value.latest || "")
  const current = normalizeVersion(changelogTag.value || "")
  return Boolean(latest) && latest === current
})

const updateStatusText = computed(() => {
  // 检查中 / 失败与版本号能否比较无关，先给状态
  if (updateState.value === "checking") return "检查中"
  if (updateState.value === "failed") return "检查失败"
  if (!updateComparable.value) {
    // dev 构建没有可比版本，判不出「是不是最新」；但「上游现在是多少」仍然有用，
    // 否则用户点了半天看到的是一片空白，会以为按钮没生效。
    const latest = updateData.value?.latest
    return latest ? `开发版 · 最新 ${latest}` : ""
  }
  switch (updateState.value) {
    case "latest":
      return "已是最新"
    case "outdated":
      return updateData.value?.latest ? `有新版本 ${updateData.value.latest}` : "有新版本"
    default:
      // ignored：下面那行「已忽略 · 恢复提示」已经说清楚了，这里不重复
      return ""
  }
})

const updateStatusClass = computed(() => {
  switch (updateState.value) {
    case "outdated":
      return props.lightTheme ? "text-amber-600" : "text-amber-300"
    case "latest":
      return props.lightTheme ? "text-emerald-600" : "text-emerald-300"
    case "failed":
      return props.lightTheme ? "text-rose-600" : "text-rose-300"
    default:
      return props.lightTheme ? "text-gray-400" : "text-slate-500"
  }
})

const updateActionLabel = computed(() => {
  if (updateState.value === "failed") return "重试"
  if (updateState.value === "latest" && updateComparable.value) return "重新检查"
  return "检查更新"
})

// 打开菜单就静默取一次结果：折叠着的「关于」行也要能亮出「有新版本」小点，
// 否则用户必须点开才会发现自己在旧版本上。
// 后端 GET 优先读 24h 缓存（每天 04:00 的定时任务已经预热），基本不碰 Gitee；
// ensureUpdateChecked 自身幂等，展开/收起反复触发也不会重复请求。
watch(
  () => props.visible,
  (visible) => {
    if (!visible || !props.repoUrl) return
    ignoreError.value = ""
    void ensureUpdateChecked(props.version)
  },
)

const onCheckUpdate = () => {
  ignoreError.value = ""
  void checkForUpdate(props.version)
}

/** 「稍后」只记在 sessionStorage：本次会话不再出现，下次打开还会提示。 */
const onDeferUpdate = () => {
  const target = updateData.value?.latest_normalized || updateData.value?.latest || ""
  if (target) deferUpdateVersion(target)
}

/** 「忽略此版本」写账号偏好；上游出现更大的 tag 时会自动重新提示。 */
const onIgnoreUpdate = async () => {
  const target = updateData.value?.latest_normalized || updateData.value?.latest || ""
  if (!target) return
  ignoringUpdate.value = true
  ignoreError.value = ""
  const ok = await ignoreUpdateVersion(target)
  ignoringUpdate.value = false
  if (!ok) ignoreError.value = "忽略失败，请稍后重试"
}

const onRestoreUpdate = () => {
  void restoreUpdatePrompt()
}

/** 当前账号能用的模式；字段缺失时按「两种都能用」兜底（旧登录快照） */
const usableModes = computed<Array<"user" | "pro">>(() =>
  props.allowedModes.length > 0 ? props.allowedModes : ["user", "pro"],
)
const isModeAllowed = (value: "user" | "pro") => usableModes.value.includes(value)

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

/** 被禁用时的悬停说明：讲清「谁关的、找谁开」，不占菜单版面 */
const modeBlockedReason = computed(() => {
  const who = props.blockedBy.length ? `角色「${props.blockedBy[0]}」` : "管理员统一设置"
  return `由${who}关闭，需要恢复请联系管理员`
})

/** 卡片三态：可用 / 当前选中 / 被禁用；禁用态用虚线框 + 降透明，且不再响应 hover */
const modeCardClass = (value: "user" | "pro"): string => {
  const base = "flex flex-col gap-0.5 rounded-[11px] border px-2.5 py-2 text-left transition-colors"
  if (!isModeAllowed(value)) {
    return `${base} cursor-not-allowed border-dashed opacity-[0.55] ${
      props.lightTheme ? "border-gray-200 bg-gray-50/60" : "border-white/[0.08] bg-white/[0.02]"
    }`
  }
  if (props.mode === value) {
    return `${base} ${
      props.lightTheme ? "border-primary/40 bg-primary/[0.08]" : "border-primary/45 bg-primary/15"
    }`
  }
  return `${base} ${
    props.lightTheme ? "border-gray-200 hover:bg-gray-50" : "border-white/10 hover:bg-white/[0.05]"
  }`
}

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
  // 点「检查更新」后状态立刻切到「检查中」，模板会把按钮节点换掉；事件冒泡到
  // document 时 target 已经脱离文档，contains() 必然为 false，于是被当成
  // 「点了菜单外面」——表现就是「点一下检查，整个菜单关了」。
  // 已脱离文档的点击一律不算点外面。
  if (!document.body.contains(target)) return
  if (rootRef.value?.contains(target)) return
  // 触发按钮自己负责 toggle，这里跳过，否则「点开→document 又关掉」会闪
  if (target.closest("[data-account-trigger]")) return
  // 更新内容弹层被 Teleport 到 body，不在 rootRef 内：点它只是看文档，不该关菜单
  if (target.closest("[data-update-notes]")) return
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
      // 菜单关掉时弹层必须一起收：否则它被 Teleport 到 body，会孤零零留在屏幕上
      notesOpen.value = false
      // 关闭后把焦点还给头像，键盘用户不会掉到页面顶部
      lastFocused?.focus?.()
      lastFocused = null
    }
  },
)

const onDocumentKeydown = (event: KeyboardEvent) => {
  if (event.key !== "Escape" || !props.visible) return
  // 弹层盖在菜单之上：Esc 先关弹层，用户再按一次才收菜单
  if (notesOpen.value) {
    notesOpen.value = false
    return
  }
  close()
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

/* 版本检测的状态点：颜色继承父级的 text-*，深浅主题共用一套 */
.update-status-dot {
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: currentColor;
  flex: 0 0 5px;
}

/* 菜单里的文字按钮：与旁边的链接同样式，避免原生 button 的默认外观 */
.update-badge-dot {
  width: 6px;
  height: 6px;
  flex: 0 0 6px;
  border-radius: 999px;
  background: #f59e0b;
}

.update-link {
  font: inherit;
  line-height: inherit;
  color: inherit;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.update-link:disabled {
  opacity: 0.55;
  cursor: default;
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
