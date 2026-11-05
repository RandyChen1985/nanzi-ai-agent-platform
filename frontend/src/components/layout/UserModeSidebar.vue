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
        <!-- 移动端是抽屉，没有「折叠」这个概念：收起由顶栏汉堡负责 -->
        <button
          v-if="!mobile"
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

    <!--
      菜单区固定、任务区独立滚动。

      之前两者共用一个滚动容器：任务多或窗口矮（分屏 / 13 寸）时菜单会被一起滚走，
      想换入口得先滚回顶部。菜单内容本身也会随「专家」「更多」展开变长，所以给它
      自己的 52vh 上限与滚动条。
    -->
    <div class="flex-shrink-0 px-2.5">
      <div class="max-h-[52vh] overflow-y-auto custom-scrollbar">
        <nav class="flex flex-col gap-0.5" aria-label="用户模式导航">
          <template v-for="item in menu" :key="item.key">
            <!-- 一级项（非分组） -->
            <button
              v-if="item.kind !== 'group'"
              type="button"
              class="flex h-10 w-full items-center rounded-[11px] text-left transition-colors"
              :class="[navItemClass(item, isItemActive(item)), collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5', !collapsed && (mobile ? 'text-[15px]' : 'text-[14px]')]"
              :aria-disabled="item.locked ? 'true' : undefined"
              :title="item.locked ? lockedHint(item.label) : item.label"
              @click="onItemClick(item)"
            >
              <MenuIcon :name="item.icon" class="h-[18px] w-[18px] flex-shrink-0" :class="item.locked ? 'opacity-40' : ''" />
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
              <!-- 分组只负责展开/收起：子项选中时父项不跟着高亮，否则一屏会有两个「选中」 -->
              <button
                type="button"
                class="flex h-10 w-full items-center rounded-[11px] text-left transition-colors"
                :class="[navItemClass(item, false), collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5', !collapsed && (mobile ? 'text-[15px]' : 'text-[14px]')]"
                :aria-expanded="expanded[item.key] !== false"
                :title="collapsed ? item.label : undefined"
                @click="toggleGroup(item)"
              >
                <MenuIcon :name="item.icon" class="h-[18px] w-[18px] flex-shrink-0" />
                <span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
                <svg
                  v-if="!collapsed"
                  class="h-3.5 w-3.5 flex-shrink-0 text-gray-400 transition-transform"
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
                  class="flex h-[34px] w-full items-center gap-2 rounded-[9px] px-2 text-left text-[13px] transition-colors"
                  :class="navSubClass(isActive(child.path), child.locked)"
                  :aria-disabled="child.locked ? 'true' : undefined"
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
                  class="mt-1 flex h-[34px] w-full items-center gap-2 rounded-[9px] px-2 text-left text-[13px] text-primary transition-colors hover:bg-primary/5"
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
      </div>
    </div>

    <!-- 会话列表（历史会话）：收起侧栏时空间不够，整块隐藏（v-show 保住滚动位置与搜索态） -->
    <div
      v-show="!collapsed"
      ref="scrollRef"
      class="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3 custom-scrollbar"
      @scroll.passive="onScroll"
    >
      <!-- 标题行：sticky，任务多的时候搜索结果也还能关掉 -->
      <div
        class="sticky top-0 z-10 flex items-center justify-between gap-2 px-2.5 py-1.5"
        :class="lightTheme ? 'bg-white' : 'bg-sidebar'"
      >
        <template v-if="!taskSearchOpen">
          <span v-if="!taskKeyword.trim()" class="text-xs text-gray-400">会话列表（{{ taskTotal }}）</span>
          <span v-else class="text-xs text-gray-400">找到 {{ taskTotal }} 条</span>
        </template>
        <label v-else class="relative flex min-w-0 flex-1 items-center">
          <svg class="pointer-events-none absolute left-1.5 h-3.5 w-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
            <circle cx="11" cy="11" r="7" />
            <path d="M16.5 16.5L21 21" stroke-linecap="round" />
          </svg>
          <input
            ref="taskSearchRef"
            v-model="taskKeyword"
            type="search"
            placeholder="搜索会话 / 智能体"
            class="w-full rounded-md border border-gray-200 bg-white py-1 pl-6 pr-1 text-xs outline-none focus:border-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            @input="onTaskSearchInput"
            @keydown.esc="closeTaskSearch"
          />
        </label>
        <button
          type="button"
          class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
          :title="taskSearchOpen ? '关闭搜索' : '搜索会话'"
          :aria-label="taskSearchOpen ? '关闭搜索' : '搜索会话'"
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

      <div
        v-if="loadingTasks && !tasks.length && !pinned.length"
        class="space-y-1.5 px-1"
      >
        <div
          v-for="index in 4"
          :key="index"
          class="h-9 animate-pulse rounded-lg"
          :class="lightTheme ? 'bg-gray-100' : 'bg-white/[0.06]'"
        />
      </div>

      <!-- 加载失败：不能伪装成「没有会话」，否则用户会以为历史丢了 -->
      <div
        v-else-if="loadError && !tasks.length"
        class="mx-1 mt-1 rounded-lg border border-rose-200/70 bg-rose-50/60 px-2.5 py-2 text-xs text-rose-600 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"
      >
        <p>会话列表加载失败。</p>
        <button type="button" class="mt-1 font-semibold underline underline-offset-2" @click="loadTasks">
          点此重试
        </button>
      </div>

      <template v-else>
        <div v-for="section in taskSections" :key="section.key">
          <p class="px-2.5 pb-1 pt-2 text-[11px] font-medium text-gray-400">{{ section.title }}</p>
          <ul class="flex flex-col gap-0.5">
            <li v-for="item in section.items" :key="taskKeyOf(item)">
              <div class="group/item">
                <button
                  type="button"
                  class="flex h-9 w-full items-center gap-1.5 rounded-[9px] px-2.5 text-left text-sm transition-colors"
                  :class="
                    isConversationActive(item)
                      ? lightTheme
                        ? 'bg-primary/10 font-semibold text-primary'
                        : 'bg-primary/15 font-semibold text-primary'
                      : lightTheme
                        ? 'text-gray-700 hover:bg-gray-100'
                        : 'text-slate-200 hover:bg-white/[0.06]'
                  "
                  :title="itemTitle(item)"
                  @click="openConversation(item)"
                >
                  <!-- 置顶项：常显小图钉，和普通会话区分开 -->
                  <svg
                    v-if="item.__pinned"
                    class="h-3 w-3 flex-shrink-0 text-primary"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path d="M14.5 2.5l7 7-2.2 2.2-1.1-1.1-3.2 3.2.4 3.5-2 2-4.7-4.7-4.2 4.2-1.4-1.4 4.2-4.2L2.6 8.5l2-2 3.5.4 3.2-3.2-1.1-1.1z" />
                  </svg>
                  <span class="min-w-0 flex-1 truncate">{{ itemTitle(item) }}</span>
                  <span class="flex-shrink-0 text-[11px] text-gray-400">{{ formatTaskTime(item.created_at) }}</span>
                  <button
                    type="button"
                    data-task-menu
                    class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-gray-400 transition-all hover:bg-gray-200/70 hover:text-gray-700 focus:opacity-100 dark:hover:bg-white/10"
                    :class="isMenuOpen(item) ? 'opacity-100' : 'opacity-100 sm:opacity-0 sm:group-hover/item:opacity-100'"
                    :title="isMenuOpen(item) ? '收起操作' : '更多操作'"
                    :aria-label="isMenuOpen(item) ? '收起操作' : '更多操作'"
                    :aria-expanded="isMenuOpen(item)"
                    @click.stop="toggleTaskMenu(item)"
                  >
                    <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                      <circle cx="5" cy="12" r="1.6" />
                      <circle cx="12" cy="12" r="1.6" />
                      <circle cx="19" cy="12" r="1.6" />
                    </svg>
                  </button>
                </button>

                <!-- 操作行内联展开：不做浮层，窄侧栏里不会被裁剪，移动端也点得到 -->
                <div
                  v-if="isMenuOpen(item)"
                  data-task-menu
                  class="mt-0.5 flex items-center gap-1 rounded-[9px] bg-gray-50 px-1.5 py-1 dark:bg-white/[0.04]"
                >
                  <button
                    type="button"
                    class="rounded px-1.5 py-0.5 text-[11px] font-medium text-gray-600 hover:bg-white hover:text-gray-900 dark:text-slate-300 dark:hover:bg-white/10"
                    @click.stop="handleTogglePin(item)"
                  >{{ isPinned(String(item.conversation_id || '')) ? '取消置顶' : '置顶' }}</button>
                  <button
                    type="button"
                    class="rounded px-1.5 py-0.5 text-[11px] font-medium text-gray-600 hover:bg-white hover:text-gray-900 dark:text-slate-300 dark:hover:bg-white/10"
                    @click.stop="openShare(item)"
                  >分享</button>
                  <button
                    type="button"
                    class="rounded px-1.5 py-0.5 text-[11px] font-medium text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10"
                    @click.stop="askDelete(item)"
                  >删除</button>
                </div>
              </div>
            </li>
          </ul>
        </div>

        <!-- 空态：文案里的「新建会话」直接做成按钮，不用再让用户回菜单找 -->
        <div
          v-if="!taskSections.length && !loadError"
          class="px-2.5 py-2 text-xs text-gray-400"
        >
          <p v-if="taskKeyword.trim()">没有匹配的会话。</p>
          <template v-else>
            <p>还没有会话。</p>
            <button
              type="button"
              class="mt-1 font-semibold text-primary hover:underline"
              @click="emit('new-chat')"
            >新建会话</button>
          </template>
        </div>

        <p v-if="loadingMore" class="px-2.5 py-2 text-center text-xs text-gray-400">正在加载…</p>
        <p
          v-else-if="tasks.length && !hasMoreTasks && !taskKeyword.trim()"
          class="px-2.5 py-2 text-center text-xs text-gray-300 dark:text-gray-600"
        >已加载全部会话</p>

        <!-- 已有列表时刷新失败：保留旧数据，只在底部提示并提供重试 -->
        <div
          v-if="loadError && tasks.length"
          class="mx-1 mt-1 flex items-center justify-between rounded-lg bg-rose-50/70 px-2 py-1 text-[11px] text-rose-600 dark:bg-rose-500/10 dark:text-rose-300"
        >
          <span>刷新失败，列表可能不是最新</span>
          <button type="button" class="font-semibold underline underline-offset-2" @click="loadTasks">重试</button>
        </div>
      </template>
    </div>

    <!-- 用户卡：左半是个人信息，右半是原来顶栏的通知 / 主题 / 退出 -->
    <div
      class="flex flex-shrink-0 items-center gap-1.5 border-t p-2"
      :class="[
        collapsed ? 'flex-col' : 'flex-row',
        lightTheme ? 'border-gray-200/80 bg-white' : 'border-white/[0.06] bg-sidebar',
      ]"
    >
      <button
        type="button"
        class="flex min-w-0 items-center rounded-lg p-1 text-left transition-colors"
        :class="[
          collapsed ? 'flex-none justify-center' : 'flex-1 gap-2',
          lightTheme ? 'hover:bg-gray-100/80' : 'hover:bg-white/[0.06]',
        ]"
        data-account-trigger
        aria-haspopup="menu"
        :title="collapsed ? displayName : '账户与界面模式'"
        @click="emit('open-account-menu')"
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
          <p class="mt-0.5 truncate text-[11px] leading-none text-gray-400">{{ roleLabel }}</p>
        </div>
      </button>

      <!-- 收起态：通知 / 主题 / 退出竖排保留，否则想退出登录还得先把侧栏展开 -->
      <template v-if="collapsed">
        <PortalNotificationBell panel-placement="up" class="flex-shrink-0" />
        <button
          type="button"
          class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
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
          class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-white/[0.06]"
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

      <template v-else>
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

    <!-- 分享会话：复用聊天页同一个对话框（门户页不传 authHeaders，走 Cookie） -->
    <ShareConversationDialog
      v-if="shareTarget"
      :visible="shareVisible"
      :conversation-id="String(shareTarget.conversation_id || '')"
      :conversation-title="itemTitle(shareTarget)"
      :turn-count="Number(shareTarget.turn_count || 0)"
      :running="false"
      @close="closeShare"
      @created="handleShareCreated"
      @toast="handleShareToast"
    />

    <!-- 删除确认 -->
    <ConfirmModal
      v-if="deleteTarget"
      title="删除会话"
      :message="`「${itemTitle(deleteTarget)}」将被永久删除，删除后无法恢复。`"
      confirm-text="删除"
      type="danger"
      :loading="deleting"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue"
import { useRoute } from "vue-router"
import axios from "@/utils/axios"
import MenuIcon from "@/components/menu/MenuIcon.vue"
import PortalNotificationBell from "@/components/PortalNotificationBell.vue"
import ConfirmModal from "@/components/ConfirmModal.vue"
import { useBranding } from "@/composables/useBranding"
import { usePinnedConversations } from "@/composables/chat/usePinnedConversations"
import {
  USER_MODE_TASK_PAGE_SIZE,
  computeUserModeTaskPageSize,
  resolveUserModeMenu,
  type ResolvedUserModeChild,
  type ResolvedUserModeItem,
} from "@/constants/userModeMenu"

/**
 * 分享对话框带二维码生成（qrcode）与分享表单，对「只是看看任务」的用户是纯负担。
 * 异步加载：点「分享」时才拉这个 chunk。
 */
const ShareConversationDialog = defineAsyncComponent(
  () => import("@/components/ShareConversationDialog.vue"),
)

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
  /** 移动端的抽屉形态：没有折叠按钮，点击菜单后由父级关闭抽屉 */
  mobile?: boolean
}>()

const emit = defineEmits<{
  (e: "navigate", path: string): void
  (e: "new-chat"): void
  (e: "open-conversation", item: ChatHistoryItem): void
  (e: "open-account-menu"): void
  (e: "switch-mode", mode: "user" | "pro"): void
  (e: "toggle-theme"): void
  (e: "logout"): void
  (e: "toggle-collapse"): void
  (e: "toast", message: string, type?: "success" | "warning" | "error"): void
}>()

interface ChatHistoryItem {
  conversation_id?: string | null
  trace_id?: string
  query?: string | null
  title?: string | null
  created_at?: string
  turn_count?: number
  agent_name?: string
  /** 本地标记：这一条来自置顶快照 / 当前在置顶区 */
  __pinned?: boolean
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
const EXPANDED_CACHE_PREFIX = "nanzi_user_mode_expanded"
/** 按 user_id 分键：换号登录不能继承上一个账号展开着哪些组 */
const expandedCacheKey = (userId: string) =>
  `${EXPANDED_CACHE_PREFIX}:${userId || "anon"}`

function readExpandedCache(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(expandedCacheKey(props.userId))
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === "object" ? parsed : {}
  } catch {
    return {}
  }
}

const expanded = reactive<Record<string, boolean>>({
  experts: false,
  more: false,
  ...readExpandedCache(),
})

const persistExpandedState = () => {
  try {
    localStorage.setItem(
      expandedCacheKey(props.userId),
      JSON.stringify({ ...expanded }),
    )
  } catch {
    /* 隐私模式等写不进去，忽略：只是少一层「切模式回来还展开着」 */
  }
}

/**
 * 路由变化时，只让「当前所在的那一组」保持展开，其余收起。
 *
 * 之前只写 `expanded[key] = true`、从不置回 false：点开过「更多」之后，
 * 再去点定时任务/知识库，那一组会一直挂在下面，看起来像「点别的菜单把更多也展开了」。
 * 手动点父项展开不受这里影响——那条路径不改路由。
 *
 * 首次同步（含从专业模式切回用户模式、侧栏重新挂载）保留上次手动展开的组：
 * 否则每次切模式回来，用户展开的「更多」都会被收起。
 */
let firstRouteSync = true
watch(
  () => route.path,
  (path) => {
    for (const item of menu.value) {
      if (item.kind !== "group" || !item.children) continue
      const containsCurrent = item.children.some((child) => pathMatches(path, child.path))
      if (firstRouteSync && expanded[item.key]) {
        expanded[item.key] = true
        continue
      }
      expanded[item.key] = containsCurrent
    }
    firstRouteSync = false
    persistExpandedState()
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

/**
 * 「新建会话」只在「聊天页 + 没有打开任何会话」时高亮。
 *
 * 聊天页的路径恒为 /dashboard/chat，打开历史会话只改 query；按路径判断的话
 * 「新建会话」会和当前会话同时亮，用户分不清自己到底在新会话还是历史里。
 */
const isNewChatActive = computed(
  () => route.path === "/dashboard/chat" && !String(props.activeConversationId || ""),
)

const isItemActive = (item: ResolvedUserModeItem): boolean =>
  item.kind === "new-chat" ? isNewChatActive.value : isActive(item.path)

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
    persistExpandedState()
    const first = (item.children || []).find((child) => !child.locked)
    if (first) emit("navigate", first.path)
    return
  }
  expanded[item.key] = !isOpen
  persistExpandedState()
}

const navItemClass = (item: ResolvedUserModeItem, active: boolean): string => {
  if (item.locked) return props.lightTheme ? "text-gray-400 cursor-not-allowed" : "text-slate-500 cursor-not-allowed"
  if (active) {
    return props.lightTheme
      ? "bg-gray-200/80 font-medium text-gray-900"
      : "bg-white/10 font-medium text-white"
  }
  return props.lightTheme
    ? "font-medium text-gray-800 hover:bg-gray-100"
    : "font-medium text-slate-200 hover:bg-white/[0.06]"
}

const navSubClass = (active: boolean, locked: boolean): string => {
  if (locked) return props.lightTheme ? "text-gray-300 cursor-not-allowed" : "text-slate-600 cursor-not-allowed"
  if (active) {
    return props.lightTheme
      ? "bg-gray-200/80 font-medium text-gray-900 shadow-[inset_2px_0_0_0_#1677ff]"
      : "bg-white/10 font-medium text-white"
  }
  return props.lightTheme ? "text-gray-600 hover:bg-gray-100" : "text-slate-300 hover:bg-white/[0.06]"
}

const lockedHint = (label: string) => `当前账号没有「${label}」权限，请联系管理员开通`

const onItemClick = (item: ResolvedUserModeItem) => {
  if (item.locked) {
    // 不能用 disabled：disabled 的 button 不派发 click，提示就永远弹不出来
    emit("toast", lockedHint(item.label), "warning")
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
    emit("toast", lockedHint(child.label), "warning")
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
const loadError = ref(false)
const pageSize = ref(USER_MODE_TASK_PAGE_SIZE)
const scrollRef = ref<HTMLElement | null>(null)

/** 每次 loadTasks 自增；回包时对不上就丢掉，避免慢请求覆盖新结果 */
let loadSerial = 0

const pinnedApi = usePinnedConversations({ client: axios })
const pinned = pinnedApi.pinned
const isPinned = pinnedApi.isPinned

const taskKeyword = ref("")
const taskSearchOpen = ref(false)
const taskSearchRef = ref<HTMLInputElement | null>(null)
/** 用户自己点开的那条会话：它不在当前列表里（比如从置顶区点开）时不该触发重新拉取 */
const selfOpenedConversationId = ref("")

const fetchTaskPage = async (page: number) => {
  const params: Record<string, any> = {
    page,
    page_size: pageSize.value,
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
  const serial = ++loadSerial
  loadingTasks.value = true
  // 重新拉第一页时，上一轮「加载更多」已经作废：不重置的话底部会一直停在「正在加载…」
  loadingMore.value = false
  loadError.value = false
  tasksPage.value = 1
  try {
    const data = await fetchTaskPage(1)
    if (serial !== loadSerial) return
    const items = Array.isArray(data.items) ? data.items : []
    tasks.value = items
    taskTotal.value = Number(data.total ?? items.length) || items.length
    // 不足一页 = 没有更多了
    hasMoreTasks.value = items.length >= pageSize.value
    tasksPage.value = 2
  } catch {
    if (serial !== loadSerial) return
    // 保留旧列表：刷新失败不等于「没有任务」
    loadError.value = true
    hasMoreTasks.value = false
  } finally {
    if (serial === loadSerial) loadingTasks.value = false
  }
}

/** 追加下一页 */
const loadMoreTasks = async () => {
  if (loadingMore.value || loadingTasks.value || !hasMoreTasks.value) return
  const serial = loadSerial
  loadingMore.value = true
  try {
    const data = await fetchTaskPage(tasksPage.value)
    // 期间换过关键词 / 重新加载过：这页数据属于旧查询，直接丢弃
    if (serial !== loadSerial) return
    const items = Array.isArray(data.items) ? data.items : []
    const seen = new Set(tasks.value.map((item) => taskKeyOf(item)))
    const fresh = items.filter((item) => !seen.has(taskKeyOf(item)))
    tasks.value = [...tasks.value, ...fresh]
    taskTotal.value = Number(data.total ?? taskTotal.value) || taskTotal.value
    hasMoreTasks.value = items.length >= pageSize.value
    tasksPage.value += 1
  } catch {
    if (serial !== loadSerial) return
    // 失败就停在当前列表，不反复重试；用户滚动一次会再触发
    hasMoreTasks.value = false
  } finally {
    if (serial === loadSerial) loadingMore.value = false
  }
}

/**
 * 滚到底部自动加载下一页。
 *
 * 只监听任务区自己的滚动容器（菜单已拆成独立容器）：距离底部 48px 内补一页，
 * 够用户在列表末尾继续滑下去。
 */
const onScroll = () => {
  const el = scrollRef.value
  if (!el) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) void loadMoreTasks()
}

/** 视口高度变化时调整每页条数；条数变了就重新拉第一页，避免列表只显示半屏 */
let resizeTimer: ReturnType<typeof setTimeout> | null = null
const syncPageSize = () => {
  const next = computeUserModeTaskPageSize(window.innerHeight)
  if (next === pageSize.value) return
  pageSize.value = next
  void loadTasks()
}
const handleResize = () => {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(syncPageSize, 200)
}

const taskKeyOf = (item: ChatHistoryItem): string =>
  String(item.conversation_id || item.trace_id || "")

const itemTitle = (item: ChatHistoryItem): string =>
  String(item.title || item.query || "未命名对话")

const parseTime = (value?: string): number | null => {
  if (!value) return null
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? null : parsed
}

/**
 * 任务时间：今天显示时刻、昨天显示「昨天」、同年显示月日、跨年带上年份。
 *
 * 之前全部是 `M月D日`：今天发的和去年同一天发的长得一模一样，也没有「刚刚」的感知。
 */
const formatTaskTime = (value?: string): string => {
  const ts = parseTime(value)
  if (ts === null) return ""
  const date = new Date(ts)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (ts >= startOfToday) {
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`
  }
  if (ts >= startOfToday - 24 * 60 * 60 * 1000) return "昨天"
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日`
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
}

interface TaskSection {
  key: string
  title: string
  items: ChatHistoryItem[]
}

/**
 * 任务分组：置顶 / 今天 / 昨天 / 更早。
 *
 * 置顶项优先用当前列表里的实时数据（标题、时间更新过也能反映），列表里没有时
 * 退回置顶快照——否则第 5 页的置顶会话会从侧栏里消失。
 * 搜索状态下只显示命中结果，不塞置顶区，避免「搜不到却看到一堆置顶」。
 */
const taskSections = computed<TaskSection[]>(() => {
  const keyword = taskKeyword.value.trim()
  const pinnedIds = new Set(pinned.value.map((entry) => entry.conversation_id))
  const sections: TaskSection[] = []

  if (!keyword && pinned.value.length) {
    sections.push({
      key: "pinned",
      title: "置顶",
      items: pinned.value.map((entry) => {
        const live = tasks.value.find(
          (item) => String(item.conversation_id || "") === entry.conversation_id,
        )
        if (live) return { ...live, __pinned: true }
        return {
          conversation_id: entry.conversation_id,
          query: entry.title || "未命名对话",
          created_at: entry.created_at || "",
          turn_count: entry.turn_count,
          agent_name: entry.agent_name,
          __pinned: true,
        }
      }),
    })
  }

  const buckets: Record<"today" | "yesterday" | "earlier", ChatHistoryItem[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  }
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  for (const item of tasks.value) {
    const id = String(item.conversation_id || "")
    if (!keyword && pinnedIds.has(id)) continue
    const ts = parseTime(item.created_at)
    if (ts === null) {
      buckets.earlier.push(item)
      continue
    }
    if (ts >= startOfToday) buckets.today.push(item)
    else if (ts >= startOfToday - 24 * 60 * 60 * 1000) buckets.yesterday.push(item)
    else buckets.earlier.push(item)
  }

  const titles: Array<[keyof typeof buckets, string]> = [
    ["today", "今天"],
    ["yesterday", "昨天"],
    ["earlier", "更早"],
  ]
  for (const [key, title] of titles) {
    if (buckets[key].length) sections.push({ key, title, items: buckets[key] })
  }
  return sections
})

const openConversation = (item: ChatHistoryItem) => {
  const id = String(item.conversation_id || "")
  if (!id) return
  selfOpenedConversationId.value = id
  menuTaskKey.value = ""
  emit("open-conversation", item)
}

const isConversationActive = (item: ChatHistoryItem): boolean =>
  Boolean(
    props.activeConversationId &&
      String(item.conversation_id || "") === props.activeConversationId,
  )

/**
 * 挂件里新建 / 切换会话后，Chat.vue 会把新的 conversation_id 写回 URL。
 * 这里看到 URL 变了就刷新列表：新建的会话能立刻出现在「今天」里。
 * 自己点开的会话不重复拉取（那会把搜索结果重置掉）。
 */
watch(
  () => String(route.query.conversation_id || ""),
  (id) => {
    if (!id) return
    if (id === selfOpenedConversationId.value) {
      selfOpenedConversationId.value = ""
      return
    }
    refreshTasksForConversation(id)
  },
)

/* ---------------- 新会话落库后刷新任务列表 ---------------- */
let taskRefreshTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 新建会话时 URL 在首条消息之前就有 id 了，而历史记录要等这一轮结束才落库：
 * 只靠路由 watch 会在「还没落库」的瞬间拉一次列表，之后不再刷新（用户得手动刷页）。
 * 这里按 id 补刷几次，找到就停；挂件每轮结束还会再报一次会话（CONVERSATION_ACTIVE），
 * 触发同一个入口，长回复也不会漏。
 */
const refreshTasksForConversation = (id: string) => {
  if (!id) return
  const exists = () => tasks.value.some((item) => String(item.conversation_id || "") === id)
  if (exists()) return
  if (taskRefreshTimer) {
    clearTimeout(taskRefreshTimer)
    taskRefreshTimer = null
  }
  void loadTasks()
  let attempt = 0
  const retry = () => {
    if (attempt >= 3) return
    attempt += 1
    taskRefreshTimer = setTimeout(() => {
      taskRefreshTimer = null
      if (exists()) return
      void loadTasks()
      retry()
    }, 1500 * attempt)
  }
  retry()
}

const onConversationActive = (event: Event) => {
  const detail = (event as CustomEvent<{ conversationId?: string }>).detail
  const id = String(detail?.conversationId || "").trim()
  if (id) refreshTasksForConversation(id)
}

/* ---------------- 任务操作（置顶 / 分享 / 删除） ---------------- */
const menuTaskKey = ref("")
const isMenuOpen = (item: ChatHistoryItem): boolean => menuTaskKey.value === taskKeyOf(item)
const toggleTaskMenu = (item: ChatHistoryItem) => {
  const key = taskKeyOf(item)
  menuTaskKey.value = menuTaskKey.value === key ? "" : key
}

const onDocumentClick = (event: MouseEvent) => {
  if (!menuTaskKey.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest("[data-task-menu]")) return
  menuTaskKey.value = ""
}

const handleTogglePin = async (item: ChatHistoryItem) => {
  const result = await pinnedApi.toggle(item)
  menuTaskKey.value = ""
  if (!result.ok) {
    emit("toast", result.message || "置顶失败，请稍后重试", "error")
    return
  }
  emit("toast", result.pinned ? "已置顶" : "已取消置顶", "success")
}

const shareTarget = ref<ChatHistoryItem | null>(null)
const shareVisible = ref(false)
const openShare = (item: ChatHistoryItem) => {
  if (!item.conversation_id) {
    emit("toast", "该记录没有会话 ID，暂不支持分享", "warning")
    return
  }
  shareTarget.value = item
  shareVisible.value = true
  menuTaskKey.value = ""
}
const closeShare = () => {
  shareVisible.value = false
  shareTarget.value = null
}
const handleShareCreated = () => {
  emit("toast", "分享链接已创建", "success")
  closeShare()
}
const handleShareToast = (message: string, type: "success" | "error" | "info" | "warning") => {
  emit("toast", message, type === "warning" ? "warning" : type === "error" ? "error" : "success")
}

const deleteTarget = ref<ChatHistoryItem | null>(null)
const deleting = ref(false)
const askDelete = (item: ChatHistoryItem) => {
  if (!item.conversation_id) return
  deleteTarget.value = item
  menuTaskKey.value = ""
}
const confirmDelete = async () => {
  const item = deleteTarget.value
  if (!item) return
  const conversationId = String(item.conversation_id || "")
  if (!conversationId) {
    deleteTarget.value = null
    return
  }
  deleting.value = true
  try {
    await axios.post("/api/v1/chat/history/batch-delete", {
      conversation_ids: [conversationId],
    })
    tasks.value = tasks.value.filter(
      (entry) => String(entry.conversation_id || "") !== conversationId,
    )
    taskTotal.value = Math.max(0, taskTotal.value - 1)
    // 置顶区不能留下点开必然为空的僵尸条目
    await pinnedApi.removeMany([conversationId])
    emit("toast", "会话已删除", "success")
    if (String(props.activeConversationId || "") === conversationId) {
      // 删掉的是当前会话：让父级开一条新会话，否则挂件还停在被删的对话上
      emit("new-chat")
    }
  } catch {
    emit("toast", "删除失败，请稍后重试", "error")
  } finally {
    deleting.value = false
    deleteTarget.value = null
  }
}

const displayName = computed(() => props.realName || props.userName || "未登录")
const initial = computed(() => Array.from(props.realName || props.userName || "U").slice(-1).join(""))
/** 副标题显示角色，和专业模式用户卡保持一致（原来是 @username） */
const roleLabel = computed(() =>
  String(props.role || "").toLowerCase() === "admin" ? "管理员" : "普通用户",
)

onMounted(() => {
  pageSize.value = computeUserModeTaskPageSize(window.innerHeight)
  document.addEventListener("click", onDocumentClick)
  window.addEventListener("resize", handleResize)
  window.addEventListener("nanzi:conversation-active", onConversationActive)
  void pinnedApi.load()
  void loadTasks()
})

onUnmounted(() => {
  document.removeEventListener("click", onDocumentClick)
  window.removeEventListener("resize", handleResize)
  window.removeEventListener("nanzi:conversation-active", onConversationActive)
  if (taskRefreshTimer) {
    clearTimeout(taskRefreshTimer)
    taskRefreshTimer = null
  }
  if (resizeTimer) clearTimeout(resizeTimer)
  if (taskSearchTimer) clearTimeout(taskSearchTimer)
})

// 从聊天页回到列表时刷新一次：刚发起的会话可能才刚写进历史
watch(() => route.path, (path) => {
  if (path === "/dashboard/chat") void loadTasks()
})
</script>
