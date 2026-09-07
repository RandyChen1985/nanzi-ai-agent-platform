<template>
  <teleport to="body">
    <div
      v-if="visible"
      :class="[
        pinned
          ? 'fixed inset-y-0 right-0 z-[145] flex pointer-events-none'
          : 'fixed inset-0 z-[145] overflow-hidden',
      ]"
    >
      <div
        v-if="!pinned"
        class="absolute inset-0 bg-gray-500/30 backdrop-blur-[1px]"
        @click="emit('close')"
      />
      <aside
        :class="[
          pinned
            ? 'h-full flex pointer-events-auto'
            : 'absolute inset-y-0 right-0 flex w-full max-w-full sm:w-auto',
        ]"
      >
        <section
          :style="panelStyle"
          :class="[
            'relative z-10 flex min-h-0 flex-col border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900',
            isResizing ? 'select-none transition-none' : 'transition-all duration-300',
            isMobile
              ? 'h-full w-full border-0'
              : 'h-full w-screen max-w-[calc(100vw-300px)] border-l',
          ]"
          aria-label="服务端浏览器"
        >
          <div v-if="isResizing" class="fixed inset-0 z-[300] cursor-col-resize select-none" />
          <div
            v-if="showCloseSessionConfirm"
            class="absolute inset-0 z-[260] flex items-center justify-center bg-slate-950/30 p-4"
            @click.self="showCloseSessionConfirm = false"
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="browser-close-session-title"
              class="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-4 shadow-2xl dark:border-gray-700 dark:bg-gray-900"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div id="browser-close-session-title" class="text-sm font-bold text-gray-900 dark:text-gray-100">结束浏览器会话？</div>
                  <div class="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">远程浏览器会关闭，Profile 和 Cookie 会保留，下次可以重新打开。</div>
                </div>
                <button
                  type="button"
                  class="rounded-md px-1.5 py-0.5 text-lg leading-none text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                  aria-label="关闭确认弹窗"
                  title="关闭确认弹窗"
                  @click="showCloseSessionConfirm = false"
                >
                  ×
                </button>
              </div>
              <div class="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
                  @click="showCloseSessionConfirm = false"
                >
                  取消
                </button>
                <button
                  type="button"
                  class="rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-950/70"
                  title="关闭浏览器并彻底删除存储的 Cookie 与登录状态"
                  @click="confirmCloseSession(true)"
                >
                  重置登录与缓存
                </button>
                <button
                  type="button"
                  class="rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
                  @click="confirmCloseSession(false)"
                >
                  仅结束会话
                </button>
              </div>
            </div>
          </div>
          <div
            v-if="!isMobile"
            class="absolute bottom-0 left-0 top-0 z-50 flex w-3 -translate-x-1/2 cursor-col-resize select-none items-center justify-center touch-none"
            :class="isResizing ? 'bg-primary/30' : 'hover:bg-primary/20'"
            title="左右拖拽调整浏览器宽度（双击重置）"
            @mousedown="startResize"
            @dblclick="resetWidth"
          >
            <div
              class="flex h-8 w-1 flex-col items-center justify-center gap-0.5 rounded-full transition-all"
              :class="isResizing ? 'scale-110 bg-primary shadow-sm' : 'bg-gray-300 group-hover:bg-primary dark:bg-gray-600'"
            >
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
            </div>
          </div>
          <header class="relative flex h-12 shrink-0 items-center justify-between border-b border-gray-200 px-3 dark:border-gray-700">
            <div class="flex min-w-0 items-center gap-2">
              <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300">⌁</span>
              <div class="min-w-0">
                <div class="truncate text-xs font-bold text-gray-800 dark:text-gray-100">服务端浏览器</div>
                <div class="flex items-center gap-1.5 text-[10px] text-gray-400">
                  <span :class="connected ? 'bg-emerald-500' : 'bg-amber-500'" class="h-1.5 w-1.5 rounded-full" />
                  {{ connected ? '已连接，可人工接管' : loadingStage }}
                </div>
              </div>
            </div>
            <div class="flex items-center gap-1.5">
              <span v-if="pinned && !isMobile" class="hidden rounded bg-blue-50 px-1.5 py-0.5 text-[9px] font-bold text-blue-600 dark:bg-blue-500/10 dark:text-blue-300 sm:inline-flex">已钉住</span>
              <button
                v-if="sessionId"
                type="button"
                class="rounded-md px-1.5 py-1 text-[10px] font-semibold text-gray-500 hover:bg-red-50 hover:text-red-600 dark:text-gray-400 dark:hover:bg-red-950/30 dark:hover:text-red-300"
                title="结束当前浏览器会话（保留 Profile 和 Cookie）"
                @click="showCloseSessionConfirm = true"
              >
                结束会话
              </button>
              <button
                v-if="!isMobile"
                type="button"
                class="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-300"
                :class="pinned ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-300' : ''"
                :title="pinned ? '取消钉住' : '钉住浏览器面板'"
                :aria-label="pinned ? '取消钉住' : '钉住浏览器面板'"
                @click="pinned = !pinned"
              >
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 17v5" /><path d="M9 10.7a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16h14v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.7V7a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3.7Z" />
                </svg>
              </button>
              <select
                :value="approvalMode"
                class="rounded-md border px-1.5 py-1 text-[10px] font-semibold outline-none transition-colors dark:bg-gray-800 dark:text-gray-200"
                :class="approvalMode === 'guarded'
                  ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200'
                  : 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200'"
                title="浏览器动作模式"
                @change="emit('update:approval-mode', ($event.target as HTMLSelectElement).value as ApprovalMode)"
              >
                <option value="guarded">安全确认</option>
                <option value="autopilot">自动执行</option>
              </select>
              <button class="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800" title="关闭浏览器" @click="emit('close')">×</button>
            </div>
          </header>

          <!-- Chrome 风格多标签页栏 (Tab Bar) -->
          <div
            v-if="tabs.length > 0"
            class="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-gray-200 bg-slate-100 px-2 pt-1.5 scrollbar-none dark:border-gray-800 dark:bg-slate-900/80"
            @contextmenu.prevent="openTabContextMenu($event, null)"
          >
            <div
              v-for="tab in tabs"
              :key="tab.tab_id"
              class="group relative flex max-w-44 min-w-24 items-center gap-1.5 rounded-t-lg px-2.5 py-1 text-xs transition-all cursor-pointer select-none"
              :class="tab.active
                ? 'bg-white font-semibold text-gray-800 shadow-2xs dark:bg-gray-900 dark:text-gray-100'
                : 'bg-transparent text-gray-500 hover:bg-slate-200/70 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-slate-800/70 dark:hover:text-gray-200'"
              :title="`${tab.title || '新标签页'}\n${tab.url || 'about:blank'}\n(右键可管理标签页)`"
              @click="switchTab(tab.tab_id)"
              @contextmenu.prevent="openTabContextMenu($event, tab)"
            >
              <span class="shrink-0 text-[10px] text-gray-400">🌐</span>
              <span class="min-w-0 flex-1 truncate text-[11px]">
                {{ tab.title || tab.url || '新标签页' }}
              </span>
              <button
                v-if="tabs.length > 1"
                type="button"
                class="shrink-0 rounded p-0.5 text-gray-400 opacity-60 hover:bg-gray-200 hover:text-red-600 hover:opacity-100 dark:hover:bg-gray-700 dark:hover:text-red-400"
                :title="`关闭标签页: ${tab.title}`"
                @click.stop="closeTab(tab.tab_id)"
              >
                <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <!-- 新建标签页按钮 -->
            <button
              type="button"
              class="shrink-0 rounded-md p-1 text-gray-500 hover:bg-slate-200 hover:text-gray-800 active:scale-95 dark:text-gray-400 dark:hover:bg-slate-800 dark:hover:text-gray-200"
              title="新建标签页"
              @click="newTab"
            >
              <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
            </button>
          </div>

          <!-- Chrome 风格标签页右键菜单 (Context Menu) -->
          <div
            v-if="tabContextMenu.visible"
            class="fixed z-50 min-w-44 rounded-lg border border-gray-200 bg-white/95 p-1 text-xs text-gray-700 shadow-xl backdrop-blur-md dark:border-gray-700 dark:bg-gray-900/95 dark:text-gray-200"
            :style="{ left: `${tabContextMenu.x}px`, top: `${tabContextMenu.y}px` }"
            @click.stop
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-400"
              @click="newTab(); closeTabContextMenu();"
            >
              <span>➕</span>
              <span>新建标签页</span>
            </button>
            <button
              v-if="tabContextMenu.tab"
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-400"
              @click="reloadPage(); closeTabContextMenu();"
            >
              <span>🔄</span>
              <span>重新加载</span>
            </button>

            <div class="my-1 border-t border-gray-100 dark:border-gray-800"></div>

            <button
              v-if="tabContextMenu.tab && tabs.length > 1"
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400"
              @click="closeTab(tabContextMenu.tab.tab_id); closeTabContextMenu();"
            >
              <span>❌</span>
              <span>关闭当前标签页</span>
            </button>
            <button
              v-if="tabContextMenu.tab && tabs.length > 1"
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-400"
              @click="closeOtherTabs(tabContextMenu.tab.tab_id)"
            >
              <span>🚫</span>
              <span>关闭其他标签页</span>
            </button>
            <button
              v-if="tabContextMenu.tab && isTabNotLast(tabContextMenu.tab.tab_id)"
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-400"
              @click="closeTabsToRight(tabContextMenu.tab.tab_id)"
            >
              <span>➡️</span>
              <span>关闭右侧标签页</span>
            </button>
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400"
              @click="closeAllTabs"
            >
              <span>🧹</span>
              <span>关闭所有标签页</span>
            </button>
          </div>

          <div
            v-if="approvalMode === 'guarded' && showSafetyNotice"
            role="status"
            class="absolute right-3 top-14 z-40 flex w-[min(320px,calc(100%-24px))] items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-amber-950 shadow-lg shadow-amber-900/10 dark:border-amber-700 dark:bg-amber-950/80 dark:text-amber-100"
          >
            <span class="mt-0.5 text-sm" aria-hidden="true">⚠️</span>
            <div class="min-w-0 flex-1">
              <div class="text-[11px] font-extrabold">安全确认已开启</div>
              <div class="mt-0.5 text-[10px] leading-relaxed text-amber-800 dark:text-amber-200">提交、删除、支付等高风险动作会等待你的确认。</div>
            </div>
            <button
              type="button"
              class="rounded p-0.5 text-amber-700 hover:bg-amber-200 hover:text-amber-950 dark:text-amber-200 dark:hover:bg-amber-800 dark:hover:text-white"
              aria-label="关闭安全提示"
              title="关闭安全提示"
              @click="showSafetyNotice = false"
            >
              ×
            </button>
          </div>

          <!-- 1. 常驻信息与状态卡片栏 (高度固定 h-7.5) -->
          <div
            class="flex h-[30px] shrink-0 items-center justify-between gap-3 border-b px-3 text-[10px] transition-colors"
            :class="[
              errorMessage ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300' :
              cropMode ? 'border-purple-200 bg-purple-50 text-purple-900 dark:border-purple-900/60 dark:bg-purple-950/50 dark:text-purple-200' :
              'border-sky-100 bg-sky-50/80 text-sky-900 dark:border-sky-900/60 dark:bg-sky-950/40 dark:text-sky-100'
            ]"
          >
            <!-- 左侧：声明 / 框选提示 / 错误信息 -->
            <div class="flex min-w-0 items-center gap-1.5 truncate">
              <template v-if="errorMessage">
                <span class="shrink-0 text-red-500">⚠️</span>
                <strong class="shrink-0 font-bold">提示：</strong>
                <span class="truncate font-semibold">{{ errorMessage }}</span>
              </template>
              <template v-else-if="cropMode">
                <span class="shrink-0 text-purple-600">✂️</span>
                <span class="truncate font-bold text-purple-800 dark:text-purple-200">请在画面上按住左键拖拽框选区域</span>
              </template>
              <template v-else>
                <span aria-hidden="true">▧</span>
                <strong class="shrink-0 font-bold">远程页面截图</strong>
                <span class="truncate text-sky-700 dark:text-sky-300">非实时截图，不是网页本体；点击、滚轮、键盘会转发到远程浏览器</span>
              </template>
            </div>

            <!-- 右侧：框选退出 / 自动刷新控制 -->
            <div class="flex shrink-0 items-center gap-2">
              <button
                v-if="cropMode"
                type="button"
                class="rounded border border-purple-300 bg-white/90 px-2 py-0.5 font-bold text-purple-700 hover:bg-purple-50 dark:border-purple-700 dark:bg-purple-950/60 dark:text-purple-200"
                @click="toggleCropMode"
              >
                取消框选 (Esc)
              </button>
              <template v-else>
                <span class="text-sky-600 dark:text-sky-300">
                  {{ controlOwner === 'human' ? '人工接管中，刷新已暂停' : captchaDetected ? '验证码中，刷新已暂停' : interactionInProgress ? '操作中…' : autoRefreshPaused ? '自动刷新已暂停' : '每 5 秒自动刷新' }}
                </span>
                <button
                  v-if="controlOwner !== 'human' && !captchaDetected && !interactionInProgress"
                  type="button"
                  class="rounded border border-sky-200 bg-white/80 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 hover:bg-white dark:border-sky-800 dark:bg-sky-950/60 dark:text-sky-200"
                  @click="autoRefreshPaused ? resumeAutoRefresh() : pauseAutoRefresh()"
                >
                  {{ autoRefreshPaused ? '恢复' : '暂停' }}
                </button>
              </template>
            </div>
          </div>

          <div class="flex shrink-0 items-center gap-1.5 border-b border-gray-100 px-3 py-2 dark:border-gray-800">
            <!-- 浏览器标准导航按钮：后退、前进、刷新 -->
            <div class="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                class="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-500 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                :disabled="!snapshot || !snapshot.can_go_back || isSyncing"
                title="后退"
                @click="goBack"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <button
                type="button"
                class="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-500 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                :disabled="!snapshot || !snapshot.can_go_forward || isSyncing"
                title="前进"
                @click="goForward"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M9 5l7 7-7 7" />
                </svg>
              </button>
              <button
                type="button"
                class="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                :disabled="!snapshot || isSyncing"
                title="刷新页面"
                @click="reloadPage"
              >
                <svg class="h-3.5 w-3.5" :class="{ 'animate-spin': isSyncing }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </button>
            </div>

            <input
              v-model="address"
              class="min-w-0 flex-1 rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 text-[11px] text-gray-700 outline-none focus:border-blue-400 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
              placeholder="输入网址，如 www.baidu.com"
              @keyup.enter="navigate"
            />
            <button class="rounded-md bg-blue-600 px-2.5 py-1.5 text-[10px] font-bold text-white hover:bg-blue-700" @click="navigate">打开</button>
            <button
              v-if="snapshot"
              type="button"
              class="shrink-0 rounded-md border px-2 py-1.5 text-[10px] font-medium transition-colors"
              :class="cropMode ? 'border-purple-400 bg-purple-50 text-purple-700 dark:border-purple-600 dark:bg-purple-950/60 dark:text-purple-200' : 'border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100 hover:text-purple-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700'"
              :title="cropMode ? '点击退出框选' : '框选画面区域进行 AI 视觉分析或截取复制'"
              @click="toggleCropMode"
            >
              {{ cropMode ? '✂️ 正在框选' : '✂️ 区域分析' }}
            </button>
            <button
              v-if="snapshot"
              type="button"
              class="shrink-0 rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 text-[10px] font-medium text-gray-700 hover:bg-gray-100 hover:text-blue-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
              :title="viewMode === 'fit' ? '当前为自适应缩放，点击切换 1:1 原图尺寸' : '当前为 1:1 原图，点击切换自适应窗口'"
              @click="viewMode = viewMode === 'fit' ? 'original' : 'fit'"
            >
              {{ viewMode === 'fit' ? '🔍 适合窗口' : '🔎 1:1 原图' }}
            </button>
          </div>

          <!-- 2. 常驻 AI 人机交互控制栏 (高度固定 h-8，永远专职负责交互与控制权) -->
          <div
            class="flex h-8 shrink-0 items-center justify-between gap-2 border-b px-3 text-[10px] transition-colors"
            :class="[
              controlOwner === 'human' ? 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-200' :
              'border-gray-200/80 bg-white text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300'
            ]"
          >
            <!-- 左侧：人机接管状态与焦点提示 -->
            <div class="flex min-w-0 items-center gap-1.5 truncate font-medium">
              <template v-if="controlOwner === 'human'">
                <!-- 人工当前动作实时展示 -->
                <template v-if="currentHumanAction">
                  <span class="relative flex h-2 w-2 shrink-0">
                    <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
                    <span class="relative inline-flex h-2 w-2 rounded-full bg-emerald-600"></span>
                  </span>
                  <span class="shrink-0 font-bold" :class="humanActionInfo.color">
                    {{ humanActionInfo.icon }} {{ humanActionInfo.label }}
                  </span>
                  <span v-if="captchaDetected" class="text-amber-600 dark:text-amber-400">（请完成安全验证）</span>
                  <span class="truncate text-emerald-800 dark:text-emerald-200">· {{ currentHumanAction.detail }}</span>
                </template>
                <!-- 人工待命/选定/默认态 -->
                <template v-else>
                  <span class="shrink-0 text-emerald-600">🎯</span>
                  <span class="shrink-0 font-bold">当前由人工操作</span>
                  <span v-if="captchaDetected" class="text-amber-600 dark:text-amber-400">（请完成安全验证）</span>
                  <span v-else-if="selectedElement" class="truncate text-emerald-800 dark:text-emerald-200">
                    · 已选定 <strong class="text-cyan-700 dark:text-cyan-300">#{{ selectedElement.ref }}</strong>
                    <span class="font-medium text-emerald-900 dark:text-emerald-100">{{ selectedElement.role || selectedElement.tag || '元素' }}</span>
                    <template v-if="selectedElement.name">"{{ selectedElement.name }}"</template>
                    <span class="ml-1 font-medium text-emerald-600 dark:text-emerald-400">（💡 双击可执行点击/跳转）</span>
                  </span>
                  <span v-else-if="selectedPoint" class="truncate text-emerald-800 dark:text-emerald-200">
                    · 已选定坐标 ({{ Math.round(selectedPoint.x) }}, {{ Math.round(selectedPoint.y) }})
                    <span class="ml-1 font-medium text-emerald-600 dark:text-emerald-400">（💡 双击可在此处点击）</span>
                  </span>
                  <span v-else class="truncate text-emerald-700 dark:text-emerald-300">· {{ remoteFocusMessage || '直接点击/输入操作网页' }}</span>
                </template>
              </template>
              <template v-else>
                <!-- AI 细化具体动作正在执行中 -->
                <template v-if="currentAiAction">
                  <span class="relative flex h-2 w-2 shrink-0">
                    <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75"></span>
                    <span class="relative inline-flex h-2 w-2 rounded-full bg-blue-600"></span>
                  </span>
                  <span class="shrink-0 font-bold" :class="aiActionInfo.color">
                    {{ aiActionInfo.icon }} {{ aiActionInfo.label }}
                  </span>
                  <span class="truncate text-gray-600 dark:text-gray-300">· {{ currentAiAction.detail || '处理中…' }}</span>
                </template>
                <!-- AI 待命中 / 空闲就绪 -->
                <template v-else>
                  <span class="shrink-0 text-blue-600 dark:text-blue-400">🎯</span>
                  <span class="shrink-0 font-bold text-blue-600 dark:text-blue-400">当前 AI 接管中</span>
                  <span v-if="remoteFocusMessage" class="truncate text-gray-500 dark:text-gray-400">· {{ remoteFocusMessage }}</span>
                  <span v-else class="truncate text-gray-500 dark:text-gray-400">
                    · AI 待命中，点击截图任意位置即可<span class="font-medium text-emerald-600 dark:text-emerald-400">人工接管</span>
                  </span>
                </template>
              </template>
            </div>

            <!-- 右侧：同步状态与交还 AI 按钮 -->
            <div class="flex shrink-0 items-center gap-2">
              <span v-if="isSyncing" class="inline-flex items-center gap-1 font-bold text-amber-600 dark:text-amber-400">
                <span class="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-amber-500"></span>
                <span>同步操作中…</span>
              </span>
              <button
                v-if="controlOwner === 'human'"
                type="button"
                class="rounded border border-emerald-300 bg-white/90 px-2 py-0.5 font-bold text-emerald-700 hover:bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-200"
                @click="releaseControl"
              >
                交还 AI
              </button>
            </div>
          </div>

          <div
            ref="viewportRef"
            class="relative min-h-0 flex-1 overflow-auto bg-slate-100 p-2 dark:bg-slate-950"
            tabindex="0"
            @keydown="handleKeydown"
          >
            <!-- 悬浮滚动控制器 (Floating Scroll Control Bar: 默认清晰立体悬浮，支持自由上下拖动) -->
            <div
              v-if="screenshotUrl"
              ref="scrollControllerRef"
              class="absolute right-3.5 z-30 flex flex-col items-center gap-0.5 select-none bg-white/95 dark:bg-slate-900/95 hover:bg-white dark:hover:bg-slate-900 p-1.5 rounded-2xl backdrop-blur-md border border-gray-200/90 dark:border-gray-700/90 shadow-xl shadow-slate-900/10 hover:shadow-2xl transition-[opacity,transform,box-shadow] duration-150"
              :class="[
                floatingScrollTop === null ? 'top-1/2 -translate-y-1/2' : '',
                isDraggingScrollController ? 'opacity-100 ring-2 ring-blue-400/50 shadow-2xl scale-105 cursor-grabbing' : 'opacity-85 hover:opacity-100'
              ]"
              :style="floatingScrollTop !== null ? { top: `${floatingScrollTop}px` } : undefined"
              @click.stop
            >
              <!-- 拖拽把手 (Drag Handle) -->
              <div
                class="flex h-4 w-7 cursor-grab active:cursor-grabbing items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-500 dark:hover:bg-gray-800 dark:hover:text-gray-200 transition-colors touch-none"
                title="按住上下拖拽调整浮标位置"
                aria-label="拖拽浮标位置"
                @pointerdown="onScrollControllerPointerDown"
              >
                <svg class="h-2.5 w-3.5" viewBox="0 0 16 10" fill="currentColor">
                  <circle cx="3" cy="3" r="1.2" />
                  <circle cx="8" cy="3" r="1.2" />
                  <circle cx="13" cy="3" r="1.2" />
                  <circle cx="3" cy="7" r="1.2" />
                  <circle cx="8" cy="7" r="1.2" />
                  <circle cx="13" cy="7" r="1.2" />
                </svg>
              </div>
              <div class="h-px w-4.5 bg-gray-200/80 dark:bg-gray-700/80 mb-0.5"></div>

              <button
                type="button"
                class="flex h-7 w-7 items-center justify-center rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 active:scale-90 dark:text-gray-200 dark:hover:bg-blue-900/50 dark:hover:text-blue-300 transition-all"
                title="一键直达顶部"
                aria-label="一键直达顶部"
                @click.stop="manualScroll('top')"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="18 15 12 9 6 15" /><line x1="6" y1="5" x2="18" y2="5" />
                </svg>
              </button>
              <button
                type="button"
                class="flex h-7 w-7 items-center justify-center rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 active:scale-90 dark:text-gray-200 dark:hover:bg-blue-900/50 dark:hover:text-blue-300 transition-all"
                title="向上翻页"
                aria-label="向上翻页"
                @click.stop="manualScroll('up')"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="18 15 12 9 6 15" />
                </svg>
              </button>
              <div class="h-px w-4.5 bg-gray-200/90 dark:bg-gray-700/90"></div>
              <button
                type="button"
                class="flex h-7 w-7 items-center justify-center rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 active:scale-90 dark:text-gray-200 dark:hover:bg-blue-900/50 dark:hover:text-blue-300 transition-all"
                title="向下翻页"
                aria-label="向下翻页"
                @click.stop="manualScroll('down')"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>
              <button
                type="button"
                class="flex h-7 w-7 items-center justify-center rounded-xl text-gray-700 hover:bg-blue-50 hover:text-blue-600 active:scale-90 dark:text-gray-200 dark:hover:bg-blue-900/50 dark:hover:text-blue-300 transition-all"
                title="一键直达底部"
                aria-label="一键直达底部"
                @click.stop="manualScroll('bottom')"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="6 9 12 15 18 9" /><line x1="6" y1="19" x2="18" y2="19" />
                </svg>
              </button>
            </div>

            <div v-if="screenshotUrl" class="pointer-events-none absolute bottom-3 left-3 z-10" role="note">
              <span class="select-none rounded-md bg-slate-200/50 px-2 py-0.5 text-[9px] font-normal text-slate-400 backdrop-blur-[2px] dark:bg-slate-800/40 dark:text-slate-500">
                当前为远程静态截图，非实时网页（操作存在延迟）· 严禁用于任何违法违规行为
              </span>
            </div>

            <!-- 右侧：服务端浏览器环境状态徽标与一键刷新检测 (固定在视口右下角，与左侧水印同一水平线) -->
            <div class="absolute bottom-3 right-3 z-20 flex items-center gap-1.5 select-none" role="status">

              <!-- 缓存大小胶囊 -->
              <transition name="fade-capsule">
                <div
                  v-if="panelCacheSizeDisplay !== null || panelCacheLoading"
                  class="inline-flex items-center gap-1 rounded-md border bg-white/90 px-2 py-0.5 text-[10px] shadow-xs backdrop-blur-xs transition-all dark:bg-slate-900/90"
                  :class="[
                    panelClearConfirm
                      ? 'border-red-300/80 text-red-600 dark:border-red-600/60 dark:text-red-400'
                      : 'border-slate-200/80 text-slate-500 dark:border-slate-700/80 dark:text-slate-400'
                  ]"
                  :title="panelCacheSizeBytes === 0 ? '暂无缓存可清除' : `云端浏览器已占用 ${panelCacheSizeDisplay ?? '…'}\n点击右侧 🗑️ 可清除：Cookie 与登录态、浏览历史、页面缓存\n不影响：本地 Chrome、平台账号、对话记录、工作区文件`"
                >
                  <!-- 存储图标（加载或正常状态） -->
                  <svg v-if="!panelCacheLoading && !panelClearConfirm" class="h-2.5 w-2.5 shrink-0 opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>
                  </svg>
                  <svg v-else-if="panelCacheLoading" class="h-2.5 w-2.5 shrink-0 animate-spin text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                  </svg>
                  <!-- 确认状态：警告图标 -->
                  <svg v-else class="h-2.5 w-2.5 shrink-0 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>

                  <span class="font-mono text-[9px] font-medium tracking-tight whitespace-nowrap">
                    <template v-if="panelCacheLoading">计算中…</template>
                    <template v-else-if="panelClearing">清除中…</template>
                    <template v-else-if="panelCacheSizeBytes === 0">缓存已清空</template>
                    <template v-else-if="panelClearConfirm">确认清除？</template>
                    <template v-else>缓存 {{ panelCacheSizeDisplay }}</template>
                  </span>

                  <!-- 正常状态：清除按钮 -->
                  <button
                    v-if="panelCacheSizeBytes !== 0 && !panelClearConfirm && !panelClearing"
                    type="button"
                    class="rounded p-0.5 text-slate-400 hover:text-red-500 dark:hover:text-red-400 transition-all active:scale-90"
                    title="清除云端浏览器缓存与登录态"
                    @click.stop="clearPanelCache"
                  >
                    <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
                    </svg>
                  </button>

                  <!-- 确认状态：取消 + 确认 两个按钮 -->
                  <template v-if="panelClearConfirm && !panelClearing">
                    <button
                      type="button"
                      class="rounded p-0.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-all active:scale-90"
                      title="取消"
                      @click.stop="panelClearConfirm = false"
                    >
                      <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                      </svg>
                    </button>
                    <button
                      type="button"
                      class="rounded p-0.5 text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 animate-pulse transition-all active:scale-90"
                      title="确认清除 — 将清除所有 Cookie、登录态、浏览历史和缓存；若 AI 正在执行浏览器任务将中断（不影响本地 Chrome 和平台账号）"
                      @click.stop="clearPanelCache"
                    >
                      <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"/>
                      </svg>
                    </button>
                  </template>

                  <!-- 清除中 loading -->
                  <svg v-if="panelClearing" class="h-2.5 w-2.5 animate-spin text-red-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                  </svg>
                </div>
              </transition>

              <!-- Chromium 环境胶囊 -->
              <div
                class="inline-flex items-center gap-1 rounded-md border border-slate-200/80 bg-white/90 px-2 py-0.5 text-[10px] text-slate-700 shadow-xs backdrop-blur-xs transition-all hover:bg-white dark:border-slate-700/80 dark:bg-slate-900/90 dark:text-slate-200"
                :title="envInfoTip"
              >
                <span
                  class="h-1.5 w-1.5 rounded-full shrink-0"
                  :class="[
                    envLoading ? 'bg-amber-400 animate-pulse' :
                    envInfo?.status === 'ready' ? 'bg-emerald-500 shadow-xs shadow-emerald-500/50' :
                    'bg-red-500'
                  ]"
                />
                <span class="font-mono text-[9px] font-medium tracking-tight">
                  {{ envDisplayText }}
                </span>
                <button
                  type="button"
                  class="rounded p-0.5 text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 active:scale-90 transition-all"
                  :class="{ 'animate-spin': envLoading }"
                  title="手动重新检测服务端浏览器环境与版本"
                  aria-label="刷新环境检测"
                  @click.stop="fetchEnvironmentInfo(true)"
                >
                  <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="23 4 23 10 17 10" />
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                  </svg>
                </button>
              </div>
            </div>

            <div v-if="screenshotUrl" class="relative">
              <img
                ref="imageRef"
                v-if="snapshot"
                :key="snapshot.snapshot_id"
                :src="screenshotUrl"
                alt="远程浏览器画面"
                draggable="false"
                :class="[
                  cropMode ? 'cursor-crosshair select-none touch-none rounded border border-purple-400 bg-white shadow-md ring-2 ring-purple-400/40 dark:border-purple-600' :
                  isSyncing ? 'cursor-wait select-none touch-none rounded border border-gray-200 bg-white shadow-sm dark:border-gray-700' :
                  'cursor-crosshair select-none touch-none rounded border border-gray-200 bg-white shadow-sm dark:border-gray-700',
                  viewMode === 'fit' ? 'block w-full' : 'block w-[1280px] max-w-none'
                ]"
                @click="handleImageClick"
                @dblclick="handleImageDblClick"
                @pointerdown="handleImagePointerDown"
                @pointermove="handleImagePointerMove"
                @pointerleave="handleImagePointerLeave"
                @pointerup="handleImagePointerUp"
                @pointercancel="handleImagePointerCancel"
              />
              <!-- 框选模式选区矩形与尺寸浮标 -->
              <div
                v-if="activeCropRect"
                class="pointer-events-none absolute border-2 border-dashed border-purple-500 bg-purple-500/20 shadow-md"
                :style="{
                  left: `${activeCropRect.left}%`,
                  top: `${activeCropRect.top}%`,
                  width: `${activeCropRect.width}%`,
                  height: `${activeCropRect.height}%`,
                }"
              >
                <span class="absolute -top-5 left-0 rounded bg-purple-700 px-1 py-0.5 font-mono text-[9px] font-bold text-white shadow-xs">
                  {{ Math.round(activeCropRect.pixelW) }} × {{ Math.round(activeCropRect.pixelH) }} px
                </span>
              </div>
              <!-- 点击触点涟漪动效 -->
              <span
                v-if="!cropMode"
                v-for="r in ripples"
                :key="r.id"
                class="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-blue-500 bg-blue-400/40 shadow-sm animate-ping"
                :style="{ left: `${r.x}%`, top: `${r.y}%`, width: '26px', height: '26px' }"
              />
              <!-- 已选定元素高亮框 (Selected Element: 仅保留轻量选区边框，不遮挡画面) -->
              <div
                v-if="selectedElement && selectedElementStyle && !cropMode && !pointerDragging"
                class="pointer-events-none absolute z-10 rounded border-2 border-emerald-500 bg-emerald-500/15 shadow-sm transition-all duration-100 dark:border-emerald-400 dark:bg-emerald-400/20"
                :style="selectedElementStyle"
              />
              <!-- 智能元素悬停探测与高亮 (Element Hover Inspector) -->
              <div
                v-if="hoveredElement && hoveredElementStyle && !cropMode && !pointerDragging && (!selectedElement || selectedElement.ref !== hoveredElement.ref)"
                class="pointer-events-none absolute z-10 rounded border border-cyan-400 bg-cyan-400/15 shadow-xs transition-all duration-75 ease-out dark:border-cyan-300 dark:bg-cyan-300/20"
                :style="hoveredElementStyle"
              >
                <!-- 顶部小徽标：语义 ref + role/tag + 文本预览 -->
                <div class="absolute -top-5 left-0 flex items-center gap-1 rounded bg-slate-900/90 px-1.5 py-0.5 font-mono text-[9px] text-cyan-300 shadow-md backdrop-blur-xs whitespace-nowrap">
                  <span class="font-bold text-cyan-400">#{{ hoveredElement.ref }}</span>
                  <span class="text-slate-400">·</span>
                  <span class="text-white">{{ hoveredElement.role || hoveredElement.tag || 'element' }}</span>
                  <template v-if="hoveredElement.name">
                    <span class="text-slate-400">·</span>
                    <span class="max-w-32 truncate text-slate-300" :title="hoveredElement.name">"{{ hoveredElement.name }}"</span>
                  </template>
                </div>
              </div>

              <!-- 画面右下角：实时鼠标坐标与元素标签 (仅悬停元素时展示) -->
              <div v-if="cursorCoords && hoveredElement" class="pointer-events-none absolute bottom-3 right-3 z-20 select-none">
                <span class="inline-flex max-w-xs items-center gap-1.5 rounded-md border border-slate-700/80 bg-slate-900/90 px-2 py-0.5 font-mono text-[9px] text-slate-200 shadow-md backdrop-blur-xs">
                  <span class="shrink-0 text-slate-400">📍 ({{ cursorCoords.x }}, {{ cursorCoords.y }})</span>
                  <span class="shrink-0 text-slate-500">|</span>
                  <span class="shrink-0 font-bold text-cyan-400">[{{ hoveredElement.ref }}]</span>
                  <span class="shrink-0 text-emerald-400">{{ hoveredElement.role || hoveredElement.tag }}</span>
                </span>
              </div>
            </div>
            <!-- 环境未安装向导卡片 -->
            <div v-else-if="isEnvMissingError" class="flex h-full min-h-[360px] items-center justify-center p-6 text-xs select-none">
              <div class="w-full max-w-md rounded-2xl border border-amber-200/90 bg-white/95 p-5 shadow-xl shadow-amber-900/10 backdrop-blur-md dark:border-amber-700/80 dark:bg-gray-900/95">
                <div class="flex items-center gap-2.5 border-b border-amber-100 pb-3 dark:border-gray-800">
                  <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-lg dark:bg-amber-950/80">
                    🛠️
                  </div>
                  <div>
                    <h3 class="text-xs font-bold text-gray-900 dark:text-gray-100">服务端浏览器环境未就绪</h3>
                    <p class="mt-0.5 text-[10px] text-gray-500 dark:text-gray-400">开启远程浏览器与网页自动化需在服务器安装相关组件</p>
                  </div>
                </div>

                <div class="mt-3.5 space-y-2.5">
                  <div class="rounded-xl border border-gray-100 bg-slate-50 p-2.5 dark:border-gray-800 dark:bg-slate-950/60">
                    <div class="flex items-center justify-between text-[11px] font-medium text-gray-700 dark:text-gray-300">
                      <span>1. 安装 Python Playwright 依赖</span>
                      <button
                        type="button"
                        class="rounded px-1.5 py-0.5 text-[10px] font-bold text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950/50"
                        @click="copyInstallCmd('pip install playwright')"
                      >
                        {{ copiedCmd === 'pip install playwright' ? '✓ 已复制' : '复制命令' }}
                      </button>
                    </div>
                    <code class="mt-1 block font-mono text-[11px] font-semibold text-slate-800 dark:text-slate-200">
                      pip install playwright
                    </code>
                  </div>

                  <div class="rounded-xl border border-gray-100 bg-slate-50 p-2.5 dark:border-gray-800 dark:bg-slate-950/60">
                    <div class="flex items-center justify-between text-[11px] font-medium text-gray-700 dark:text-gray-300">
                      <span>2. 下载 Chromium 浏览器内核</span>
                      <button
                        type="button"
                        class="rounded px-1.5 py-0.5 text-[10px] font-bold text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950/50"
                        @click="copyInstallCmd('playwright install chromium')"
                      >
                        {{ copiedCmd === 'playwright install chromium' ? '✓ 已复制' : '复制命令' }}
                      </button>
                    </div>
                    <code class="mt-1 block font-mono text-[11px] font-semibold text-slate-800 dark:text-slate-200">
                      playwright install chromium
                    </code>
                  </div>

                  <div class="rounded-lg bg-amber-50/70 px-2.5 py-1.5 text-[10px] leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                    💡 Linux 生产环境若缺少系统底层依赖库，可补充执行：
                    <code class="font-mono font-bold">playwright install-deps chromium</code>
                  </div>

                  <div v-if="installLogs.length" class="rounded-xl border border-slate-200 bg-slate-950 p-2.5 text-[10px] text-slate-200 dark:border-slate-700">
                    <div class="mb-1 flex items-center justify-between font-semibold text-slate-300">
                      <span>{{ browserInstallStatus === 'running' ? '安装日志（实时）' : '安装结果' }}</span>
                      <span v-if="browserInstallStatus === 'running'" class="text-amber-300">安装中…</span>
                      <span v-else-if="browserInstallStatus === 'success'" class="text-emerald-300">已完成</span>
                      <span v-else class="text-red-300">失败</span>
                    </div>
                    <pre
                      ref="installLogsRef"
                      class="max-h-36 overflow-auto whitespace-pre-wrap break-all font-mono leading-relaxed"
                    >{{ installLogs.join('\n') }}</pre>
                  </div>
                </div>

                <div class="mt-4 flex justify-end gap-2 border-t border-gray-100 pt-3 dark:border-gray-800">
                  <button
                    type="button"
                    class="rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                    @click="emit('close-session')"
                  >
                    关闭面板
                  </button>
                  <button
                    type="button"
                    class="rounded-xl border border-blue-200 bg-blue-50 px-3.5 py-1.5 text-xs font-bold text-blue-700 shadow-sm hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300"
                    :disabled="browserInstallStatus === 'running'"
                    @click="installBrowserEnvironment"
                  >
                    {{ browserInstallStatus === 'running' ? '正在安装…' : '管理员一键安装' }}
                  </button>
                  <button
                    type="button"
                    class="rounded-xl bg-blue-600 px-3.5 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-blue-700 active:scale-95 transition-all"
                    @click="emit('retry')"
                  >
                    已安装，重试连接
                  </button>
                </div>
              </div>
            </div>
            <div v-else class="flex h-full min-h-56 items-center justify-center px-6 text-center text-xs text-gray-400">
              <div class="w-full max-w-sm rounded-xl border border-sky-100 bg-white/80 p-5 shadow-sm dark:border-sky-900/60 dark:bg-gray-900/70">
                <div class="mx-auto mb-4 h-2 w-32 animate-pulse rounded-full bg-sky-100 dark:bg-sky-900/60" />
                <div class="mx-auto h-2 w-56 animate-pulse rounded-full bg-slate-200 dark:bg-slate-700" />
                <div class="mx-auto mt-2 h-2 w-44 animate-pulse rounded-full bg-slate-200 dark:bg-slate-700" />
                <div class="mt-4 font-semibold text-slate-600 dark:text-slate-300">{{ loadingStage }}</div>
                <div class="mt-1 text-[10px] text-slate-400 dark:text-slate-500">页面加载完成后，画面会自动显示在这里</div>
              </div>
            </div>
          </div>

          <!-- 人工输入悬浮岛 (Floating Input Island: 顶部居中显著展示，自动聚焦并支持回车一键发送) -->
          <div
            v-if="showManualInput"
            class="absolute top-3 left-1/2 -translate-x-1/2 z-40 w-[min(440px,calc(100%-24px))] select-none transition-all duration-200"
            role="dialog"
            aria-label="人工输入"
          >
            <div class="rounded-2xl border border-blue-300/90 bg-white/95 p-3.5 shadow-2xl shadow-blue-900/25 ring-4 ring-blue-500/15 backdrop-blur-md dark:border-blue-700/90 dark:bg-slate-900/95 dark:ring-blue-400/15">
              <div class="flex items-center justify-between gap-2 border-b border-gray-100 pb-2 dark:border-gray-800">
                <div class="flex min-w-0 items-center gap-1.5">
                  <span class="text-sm">✍️</span>
                  <div class="text-xs font-bold text-gray-900 dark:text-gray-100">人工输入</div>
                  <span v-if="selectedElement" class="truncate font-mono text-[10px] text-blue-600 dark:text-blue-400">
                    [#{{ selectedElement.ref }}] {{ selectedElement.name ? `"${selectedElement.name}"` : selectedElement.role }}
                  </span>
                </div>
                <button
                  type="button"
                  class="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200 transition-colors"
                  aria-label="关闭人工输入"
                  title="关闭人工输入 (Esc)"
                  @click="showManualInput = false"
                >
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
              <div class="mt-0.5 pt-1.5 text-[10px] text-gray-500 dark:text-gray-400">
                已成功激活远程输入焦点，键入文字后回车即可直接填入页面
              </div>
              <div class="mt-2 flex gap-2">
                <input
                  ref="manualInputRef"
                  v-model="manualText"
                  type="text"
                  autocomplete="off"
                  class="min-w-0 flex-1 rounded-xl border border-blue-200 bg-blue-50/50 px-3 py-2 text-xs text-gray-800 outline-none transition-all focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-400/20 dark:border-blue-800 dark:bg-blue-950/30 dark:text-gray-100 dark:focus:bg-gray-900"
                  placeholder="输入文字，回车直接发送 (Esc 关闭)"
                  @keyup.enter="sendText"
                  @keydown.esc="showManualInput = false"
                />
                <button
                  class="shrink-0 rounded-xl bg-blue-600 px-3.5 py-2 text-xs font-bold text-white shadow-sm hover:bg-blue-700 active:scale-95 transition-all"
                  @click="sendText"
                >
                  发送
                </button>
              </div>
            </div>
          </div>

          <!-- 区域截图与 AI 视觉分析操作卡片 -->
          <div
            v-if="showCropCard && cropDataUrl"
            class="absolute bottom-14 left-3 right-3 z-40 sm:left-auto sm:w-96"
            role="dialog"
            aria-label="区域截图与AI视觉分析"
          >
            <div class="rounded-xl border border-purple-300 bg-white p-3.5 shadow-2xl shadow-purple-900/15 dark:border-purple-800 dark:bg-gray-900">
              <div class="flex items-start justify-between gap-2 border-b border-gray-100 pb-2 dark:border-gray-800">
                <div class="flex items-center gap-1.5 text-xs font-bold text-gray-800 dark:text-gray-100">
                  <span class="text-purple-600">✂️</span>
                  <span>区域截图与 AI 视觉分析</span>
                </div>
                <button
                  type="button"
                  class="rounded p-0.5 text-base leading-none text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                  aria-label="关闭选区卡片"
                  title="关闭选区卡片"
                  @click="cancelCrop"
                >
                  ×
                </button>
              </div>

              <div class="mt-2.5 flex items-start gap-3">
                <div class="relative shrink-0 overflow-hidden rounded-lg border border-purple-100 bg-slate-50 p-0.5 dark:border-purple-950 dark:bg-slate-950">
                  <img :src="cropDataUrl" class="max-h-20 max-w-28 rounded object-contain" alt="选区截图" />
                </div>
                <div class="min-w-0 flex-1">
                  <div class="text-[10px] text-gray-500 dark:text-gray-400">已截取选区画面，可向 AI 提问、复制或下载保存。</div>
                  <div class="mt-2 flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-semibold text-gray-700 hover:border-purple-300 hover:bg-purple-50 hover:text-purple-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-purple-600"
                      @click="copyCropImage"
                    >
                      {{ isCopied ? '✅ 已复制' : '📋 复制图片' }}
                    </button>
                    <button
                      type="button"
                      class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-semibold text-gray-700 hover:border-purple-300 hover:bg-purple-50 hover:text-purple-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-purple-600"
                      @click="downloadCropImage"
                    >
                      ⬇️ 保存图片
                    </button>
                    <button
                      type="button"
                      class="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] text-gray-500 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400"
                      @click="reCrop"
                    >
                      🔄 重新框选
                    </button>
                  </div>
                </div>
              </div>

              <!-- 问 AI 提问输入框 -->
              <div class="mt-3">
                <div class="flex gap-1.5">
                  <input
                    ref="cropInputRef"
                    v-model="cropQuestion"
                    type="text"
                    placeholder="向 AI 提问此画面内容，如：提取文字/分析图表"
                    class="min-w-0 flex-1 rounded-md border border-purple-200 bg-purple-50/30 px-2.5 py-1.5 text-xs text-gray-800 outline-none focus:border-purple-400 dark:border-purple-900/60 dark:bg-purple-950/20 dark:text-gray-200"
                    @keyup.enter="askAiWithCrop"
                    @keydown.esc="cancelCrop"
                  />
                  <button
                    type="button"
                    class="shrink-0 rounded-md bg-purple-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-purple-700 active:scale-95 disabled:opacity-50"
                    :disabled="isAnalyzing"
                    @click="askAiWithCrop"
                  >
                    {{ isAnalyzing ? '处理中…' : '✨ 问 AI' }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- 3. 底部常驻快捷键工具栏 (Bottom Quick Keys Bar) -->
          <footer class="flex h-8 shrink-0 items-center justify-between gap-1.5 border-t border-gray-200 bg-slate-50 px-2.5 dark:border-gray-800 dark:bg-slate-900/90">
            <div class="flex shrink-0 items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400">
              <span class="font-bold">⌨️ 快捷键：</span>
            </div>
            <div class="flex flex-1 items-center gap-1 overflow-x-auto scrollbar-none py-0.5">
              <button
                v-for="item in quickKeys"
                :key="item.key"
                type="button"
                class="shrink-0 rounded border border-gray-200/90 bg-white px-2 py-0.5 text-[10px] font-medium text-gray-700 shadow-2xs hover:border-blue-300 hover:bg-blue-50/80 hover:text-blue-600 active:scale-95 active:bg-blue-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-blue-500 dark:hover:bg-blue-950/50 dark:hover:text-blue-300"
                :title="item.tip"
                :disabled="!snapshot || isSyncing"
                @click="sendQuickKey(item.key, item.label)"
              >
                {{ item.label }}
              </button>
            </div>
          </footer>
        </section>
      </aside>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';

type ApprovalMode = 'guarded' | 'autopilot';
type ControlOwner = 'ai' | 'human';
type BrowserElement = {
  ref: string;
  tag?: string | null;
  role?: string | null;
  name?: string | null;
  value?: string | null;
  disabled?: boolean;
  sensitive?: boolean;
  bbox?: { x: number; y: number; width: number; height: number } | null;
};
type BrowserTab = {
  tab_id: string;
  url: string;
  title: string;
  active: boolean;
};
type BrowserSnapshot = {
  session_id: string;
  snapshot_id: string;
  tab_id?: string | null;
  url: string;
  title: string;
  screenshot_ref?: string | null;
  elements: BrowserElement[];
  scroll_x?: number;
  scroll_y?: number;
  can_go_back?: boolean;
  can_go_forward?: boolean;
  viewport_width?: number | null;
  viewport_height?: number | null;
  document_width?: number | null;
  document_height?: number | null;
  page_text?: string;
  visible_text?: string;
  page_state?: string | null;
};
type RemotePoint = { x: number; y: number };
const BROWSER_PANEL_REFRESH_INTERVAL_MS = 5000;

const props = defineProps<{
  visible: boolean;
  loading?: boolean;
  refreshSignal?: number;
  sessionId: string | null;
  viewerToken: string | null;
  approvalMode: ApprovalMode;
  environmentError?: string | null;
  authToken?: string;
}>();

const pinned = defineModel<boolean>('pinned', { default: true });
const panelWidth = defineModel<number>('panelWidth', { default: 520 });

const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'close-session', destroyProfile?: boolean): void;
  (event: 'retry'): void;
  (event: 'update:approval-mode', mode: ApprovalMode): void;
  (event: 'ask-ai-crop', payload: { image: string; question: string }): void;
}>();

const showSafetyNotice = ref(props.approvalMode === 'guarded');
const showCloseSessionConfirm = ref(false);

const socket = ref<WebSocket | null>(null);
const connected = ref(false);
const snapshot = ref<BrowserSnapshot | null>(null);
const tabs = ref<BrowserTab[]>([]);
const errorMessage = ref('');
const address = ref('');
const manualText = ref('');
type AiAction = { action: string; detail: string };
const currentAiAction = ref<AiAction | null>(null);

const AI_ACTION_CONFIG: Record<string, { icon: string; label: string; color: string }> = {
  opening: { icon: '🌐', label: 'AI 正在打开页面', color: 'text-blue-600 dark:text-blue-400' },
  reading: { icon: '👁️', label: 'AI 正在读取屏幕', color: 'text-indigo-600 dark:text-indigo-400' },
  clicking: { icon: '👆', label: 'AI 正在点击', color: 'text-amber-600 dark:text-amber-400' },
  filling: { icon: '✍️', label: 'AI 正在输入内容', color: 'text-emerald-600 dark:text-emerald-400' },
  pressing: { icon: '⌨️', label: 'AI 正在发送按键', color: 'text-purple-600 dark:text-purple-400' },
  selecting: { icon: '📋', label: 'AI 正在选择选项', color: 'text-sky-600 dark:text-sky-400' },
  scrolling: { icon: '📜', label: 'AI 正在滚动页面', color: 'text-cyan-600 dark:text-cyan-400' },
  hovering: { icon: '🎯', label: 'AI 正在悬停元素', color: 'text-teal-600 dark:text-teal-400' },
  dragging: { icon: '🖐️', label: 'AI 正在拖拽元素', color: 'text-orange-600 dark:text-orange-400' },
  waiting: { icon: '⏳', label: 'AI 正在等待加载', color: 'text-amber-600 dark:text-amber-400' },
  navigating: { icon: '🧭', label: 'AI 正在导航页面', color: 'text-blue-600 dark:text-blue-400' },
  exporting_pdf: { icon: '📄', label: 'AI 正在导出网页 PDF', color: 'text-rose-600 dark:text-rose-400' },
  extracting_table: { icon: '📊', label: 'AI 正在提取表格数据', color: 'text-emerald-600 dark:text-emerald-400' },
  executing_js: { icon: '⚡', label: 'AI 正在执行页面脚本', color: 'text-purple-600 dark:text-purple-400' },
  solving_captcha: { icon: '🛡️', label: 'AI 正在尝试自动识别验证码', color: 'text-indigo-600 dark:text-indigo-400' },
};

const aiActionInfo = computed(() => {
  const action = currentAiAction.value?.action || '';
  return AI_ACTION_CONFIG[action] || {
    icon: '⚡',
    label: 'AI 正在操作中',
    color: 'text-blue-600 dark:text-blue-400',
  };
});

type HumanAction = { action: string; detail: string };
const currentHumanAction = ref<HumanAction | null>(null);

const HUMAN_ACTION_CONFIG: Record<string, { icon: string; label: string; color: string }> = {
  click: { icon: '👆', label: '人工点击', color: 'text-emerald-700 dark:text-emerald-300' },
  scroll: { icon: '📜', label: '人工滚动', color: 'text-emerald-700 dark:text-emerald-300' },
  key: { icon: '⌨️', label: '人工按键', color: 'text-emerald-700 dark:text-emerald-300' },
  text: { icon: '✍️', label: '人工输入', color: 'text-emerald-700 dark:text-emerald-300' },
  navigate: { icon: '🧭', label: '人工导航', color: 'text-emerald-700 dark:text-emerald-300' },
  tab: { icon: '📑', label: '人工切换标签', color: 'text-emerald-700 dark:text-emerald-300' },
  drag: { icon: '🖐️', label: '人工拖拽', color: 'text-emerald-700 dark:text-emerald-300' },
};

const humanActionInfo = computed(() => {
  const action = currentHumanAction.value?.action || '';
  return HUMAN_ACTION_CONFIG[action] || {
    icon: '👆',
    label: '人工正在操作',
    color: 'text-emerald-700 dark:text-emerald-300',
  };
});

let humanActionTimer: ReturnType<typeof setTimeout> | null = null;
const setHumanAction = (action: string, detail: string, keepDuration = 2500) => {
  currentHumanAction.value = { action, detail };
  if (humanActionTimer) clearTimeout(humanActionTimer);
  if (keepDuration > 0) {
    humanActionTimer = setTimeout(() => {
      currentHumanAction.value = null;
    }, keepDuration);
  }
};

const tabContextMenu = ref<{
  visible: boolean;
  x: number;
  y: number;
  tab: BrowserTab | null;
}>({
  visible: false,
  x: 0,
  y: 0,
  tab: null,
});

const openTabContextMenu = (event: MouseEvent, tab: BrowserTab | null) => {
  event.preventDefault();
  event.stopPropagation();
  tabContextMenu.value = {
    visible: true,
    x: typeof window !== 'undefined' ? Math.min(event.clientX, window.innerWidth - 180) : event.clientX,
    y: typeof window !== 'undefined' ? Math.min(event.clientY, window.innerHeight - 220) : event.clientY,
    tab,
  };
};

const closeTabContextMenu = () => {
  tabContextMenu.value.visible = false;
};

const isTabNotLast = (tabId: string) => {
  const index = tabs.value.findIndex((t) => t.tab_id === tabId);
  return index >= 0 && index < tabs.value.length - 1;
};

const showManualInput = ref(false);
const manualInputRef = ref<HTMLInputElement | null>(null);
const remoteFocusMessage = ref('');
const controlOwner = ref<ControlOwner>('ai');

const copiedCmd = ref<string | null>(null);
const browserInstallStatus = ref<'idle' | 'running' | 'success' | 'error'>('idle');
const installLogs = ref<string[]>([]);
const installLogsRef = ref<HTMLPreElement | null>(null);

const scrollInstallLogsToBottom = async () => {
  await nextTick();
  if (installLogsRef.value) {
    installLogsRef.value.scrollTop = installLogsRef.value.scrollHeight;
  }
};

watch(
  [() => installLogs.value.length, browserInstallStatus],
  () => {
    void scrollInstallLogsToBottom();
  },
  { flush: 'post' },
);

const installBrowserEnvironment = async () => {
  if (browserInstallStatus.value === 'running') return;
  browserInstallStatus.value = 'running';
  installLogs.value = [];
  try {
    const token = props.authToken || (typeof localStorage !== 'undefined'
      ? localStorage.getItem('yovole_token') || localStorage.getItem('admin_token') || localStorage.getItem('token')
      : '');
    const response = await fetch('/api/v1/chat/browser/environment/install/stream', {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(data.detail || `安装请求失败（HTTP ${response.status}）`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((item) => item.startsWith('data: '));
        if (!line) continue;
        const event = JSON.parse(line.slice(6)) as { type?: string; line?: string; message?: string; status?: string };
        if (event.type === 'log' && event.line) installLogs.value.push(event.line);
        else if (event.message) installLogs.value.push(event.message);
        if (event.type === 'error') throw new Error(event.message || '安装失败');
        if (event.type === 'done' && event.status === 'success') browserInstallStatus.value = 'success';
      }
      if (done) break;
    }
    if (browserInstallStatus.value !== 'success') throw new Error('安装任务未正常结束');
    await fetchEnvironmentInfo(true);
    emit('retry');
  } catch (error: any) {
    browserInstallStatus.value = 'error';
    installLogs.value.push(error?.message || '安装失败');
  }
};

const copyInstallCmd = async (cmd: string) => {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      await navigator.clipboard.writeText(cmd);
      copiedCmd.value = cmd;
      setTimeout(() => {
        if (copiedCmd.value === cmd) copiedCmd.value = null;
      }, 2000);
    }
  } catch {
    // 剪贴板异常静默忽略
  }
};

const isEnvMissingError = computed(() => {
  const msg = `${errorMessage.value || ''} ${props.environmentError || ''}`.toLowerCase();
  return (
    envInfo.value?.status === 'missing_driver' ||
    envInfo.value?.status === 'missing_package' ||
    msg.includes('playwright') ||
    msg.includes('运行环境未就绪') ||
    msg.includes('chromium') ||
    msg.includes('install-deps')
  );
});

interface BrowserEnvironmentInfo {
  status: 'ready' | 'missing_package' | 'missing_driver' | 'error';
  playwright_installed: boolean;
  playwright_version: string | null;
  chromium_installed: boolean;
  chromium_executable: string | null;
  chromium_version?: string | null;
  error_detail?: string | null;
  install_guide?: string | null;
}

const envInfo = ref<BrowserEnvironmentInfo | null>(null);
const envLoading = ref(false);

const fetchEnvironmentInfo = async (manual = false) => {
  envLoading.value = true;
  try {
    const token = props.authToken || (typeof localStorage !== 'undefined'
      ? localStorage.getItem('yovole_token') || localStorage.getItem('admin_token') || localStorage.getItem('token')
      : '');
    const res = await fetch('/api/v1/chat/browser/environment', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.ok) {
      const data = await res.json() as BrowserEnvironmentInfo;
      envInfo.value = data;
      if (manual) {
        if (data.status === 'ready') {
          const vMsg = data.chromium_version ? `Chromium v${data.chromium_version}` : 'Chromium 已就绪';
          const pMsg = data.playwright_version ? ` (Playwright ${data.playwright_version})` : '';
          setHumanAction('click', `环境就绪: ${vMsg}${pMsg}`);
          remoteFocusMessage.value = `✅ 服务端浏览器环境就绪：${vMsg}${pMsg}`;
          setTimeout(() => {
            if (remoteFocusMessage.value.includes('就绪')) remoteFocusMessage.value = '';
          }, 3000);
        } else {
          setHumanAction('click', `环境未就绪: ${data.error_detail || '缺少依赖'}`);
          remoteFocusMessage.value = `⚠️ 服务端浏览器环境未就绪：${data.error_detail || '缺少依赖'}`;
        }
      }
    }
  } catch {
    if (manual) {
      remoteFocusMessage.value = '❌ 检测网络异常，请重试';
      setTimeout(() => {
        if (remoteFocusMessage.value.includes('异常')) remoteFocusMessage.value = '';
      }, 2500);
    }
  } finally {
    envLoading.value = false;
  }
};

const envDisplayText = computed(() => {
  if (envLoading.value && !envInfo.value) return '检测环境中…';
  if (!envInfo.value) return 'Chromium';
  if (envInfo.value.status === 'ready') {
    const cVer = envInfo.value.chromium_version ? `v${envInfo.value.chromium_version.split('.')[0]}` : '';
    const pVer = envInfo.value.playwright_version ? `PW ${envInfo.value.playwright_version}` : '';
    const tag = [cVer, pVer].filter(Boolean).join(' · ');
    return tag ? `Chromium ${tag}` : `Chromium (Playwright ${envInfo.value.playwright_version || '就绪'})`;
  }
  if (envInfo.value.status === 'missing_driver') {
    return '⚠️ 缺少 Chromium 内核';
  }
  if (envInfo.value.status === 'missing_package') {
    return '⚠️ 缺少 Playwright 库';
  }
  return '⚠️ 运行环境异常';
});

const envInfoTip = computed(() => {
  if (!envInfo.value) return '点击手动重新检测服务端浏览器环境与版本';
  if (envInfo.value.status === 'ready') {
    return `服务端浏览器环境就绪\nChromium 版本: ${envInfo.value.chromium_version || '已安装'}\nPlaywright 版本: ${envInfo.value.playwright_version || '未知'}\nChromium 路径: ${envInfo.value.chromium_executable || '默认'}\n点击重新检测`;
  }
  return `环境未就绪: ${envInfo.value.error_detail || '请安装相关依赖'}\n点击重新检测`;
});

// ─── 浏览器缓存大小与清理 ───────────────────────────────────────────────────
const panelCacheSizeBytes = ref<number | null>(null);
const panelCacheLoading = ref(false);
const panelClearing = ref(false);
const panelClearConfirm = ref(false); // 二次确认状态

const panelCacheSizeDisplay = computed(() => {
  const b = panelCacheSizeBytes.value;
  if (b === null) return null;
  if (b === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = b, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
});

const fetchPanelCacheSize = async () => {
  panelCacheLoading.value = true;
  try {
    const token = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : '';
    const res = await fetch('/api/v1/chat/browser/profiles', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.ok) {
      const profiles: Array<{ disk_size_bytes?: number | null }> = await res.json();
      panelCacheSizeBytes.value = profiles.reduce((acc, p) => acc + (p.disk_size_bytes ?? 0), 0);
    }
  } catch { /* 静默忽略 */ }
  finally { panelCacheLoading.value = false; }
};

const clearPanelCache = async () => {
  if (!panelClearConfirm.value) {
    // 第一次点击：进入确认状态，3 秒后自动复位
    panelClearConfirm.value = true;
    setTimeout(() => { panelClearConfirm.value = false; }, 3000);
    return;
  }
  // 第二次点击：执行清理
  panelClearConfirm.value = false;
  panelClearing.value = true;
  try {
    const token = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : '';
    const res = await fetch('/api/v1/chat/browser/profiles/clear', {
      method: 'DELETE',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.ok) {
      panelCacheSizeBytes.value = 0;
      remoteFocusMessage.value = '✅ 云端浏览器缓存已清除';
      setTimeout(() => {
        if (remoteFocusMessage.value.includes('缓存已清除')) remoteFocusMessage.value = '';
      }, 3000);
    }
  } catch { /* 静默 */ }
  finally { panelClearing.value = false; }
};
// ─────────────────────────────────────────────────────────────────────────────

const controlReason = ref<string | null>(null);
const captchaDetected = ref(false);
const interactionInProgress = ref(false);
const snapshotRequestInFlight = ref(false);
const pointerDownPoint = ref<RemotePoint | null>(null);
const lastPointerPoint = ref<RemotePoint | null>(null);
const pointerDragging = ref(false);
const suppressNextClick = ref(false);
let lastPointerMoveAt = 0;
const autoRefreshPaused = ref(false);
const viewportRef = ref<HTMLElement | null>(null);

// 增强交互状态
const viewMode = ref<'fit' | 'original'>('fit');
const isSyncing = ref(false);
let syncingTimer: ReturnType<typeof setTimeout> | null = null;
const triggerSyncing = () => {
  isSyncing.value = true;
  if (syncingTimer) clearTimeout(syncingTimer);
  syncingTimer = setTimeout(() => {
    isSyncing.value = false;
  }, 4000);
};

const cursorCoords = ref<{ x: number; y: number } | null>(null);

const findElementAtPoint = (point: { x: number; y: number }): BrowserElement | null => {
  if (!snapshot.value?.elements) return null;
  const { x, y } = point;
  let bestMatch: BrowserElement | null = null;
  let minArea = Infinity;
  for (const el of snapshot.value.elements) {
    if (!el.bbox) continue;
    const { x: bx, y: by, width: bw, height: bh } = el.bbox;
    if (x >= bx && x <= bx + bw && y >= by && y <= by + bh) {
      const area = bw * bh;
      if (area < minArea && area > 0) {
        minArea = area;
        bestMatch = el;
      }
    }
  }
  return bestMatch;
};

const hoveredElement = computed<BrowserElement | null>(() => {
  if (!cursorCoords.value || cropMode.value) return null;
  return findElementAtPoint(cursorCoords.value);
});

const hoveredElementStyle = computed(() => {
  if (!hoveredElement.value?.bbox || !snapshot.value) return null;
  const naturalW = snapshot.value.viewport_width || (imageRef.value?.naturalWidth || 1280);
  const naturalH = snapshot.value.viewport_height || (imageRef.value?.naturalHeight || 800);
  if (!naturalW || !naturalH) return null;
  const { x, y, width, height } = hoveredElement.value.bbox;
  return {
    left: `${(x / naturalW) * 100}%`,
    top: `${(y / naturalH) * 100}%`,
    width: `${(width / naturalW) * 100}%`,
    height: `${(height / naturalH) * 100}%`,
  };
});

// 人工模式选定元素状态
const selectedElement = ref<BrowserElement | null>(null);
const selectedPoint = ref<{ x: number; y: number } | null>(null);

const selectedElementStyle = computed(() => {
  if (!selectedElement.value?.bbox || !snapshot.value) return null;
  const naturalW = snapshot.value.viewport_width || (imageRef.value?.naturalWidth || 1280);
  const naturalH = snapshot.value.viewport_height || (imageRef.value?.naturalHeight || 800);
  if (!naturalW || !naturalH) return null;
  const { x, y, width, height } = selectedElement.value.bbox;
  return {
    left: `${(x / naturalW) * 100}%`,
    top: `${(y / naturalH) * 100}%`,
    width: `${(width / naturalW) * 100}%`,
    height: `${(height / naturalH) * 100}%`,
  };
});

const ripples = ref<Array<{ id: number; x: number; y: number }>>([]);
let rippleCounter = 0;
const addRipple = (event: MouseEvent | PointerEvent) => {
  const image = event.currentTarget as HTMLImageElement;
  const rect = image?.getBoundingClientRect?.();
  if (!rect || !rect.width || !rect.height) return;
  const id = ++rippleCounter;
  const x = ((event.clientX - rect.left) / rect.width) * 100;
  const y = ((event.clientY - rect.top) / rect.height) * 100;
  ripples.value.push({ id, x, y });
  setTimeout(() => {
    ripples.value = ripples.value.filter((r) => r.id !== id);
  }, 600);
};

const handleImagePointerLeave = () => {
  cursorCoords.value = null;
};

// 区域框选与 AI 视觉分析状态
const imageRef = ref<HTMLImageElement | null>(null);
const cropMode = ref(false);
const isCropping = ref(false);
const cropStartPoint = ref<{ x: number; y: number } | null>(null);
const activeCropRect = ref<{
  left: number;
  top: number;
  width: number;
  height: number;
  pixelW: number;
  pixelH: number;
  rectX: number;
  rectY: number;
  rectW: number;
  rectH: number;
} | null>(null);
const showCropCard = ref(false);
const cropDataUrl = ref('');
const cropQuestion = ref('');
const isCopied = ref(false);
const isAnalyzing = ref(false);
const cropInputRef = ref<HTMLInputElement | null>(null);

const toggleCropMode = () => {
  cropMode.value = !cropMode.value;
  if (cropMode.value) {
    showCropCard.value = false;
    activeCropRect.value = null;
    remoteFocusMessage.value = '已进入区域框选模式，请在画面上按住并拖拽鼠标框选区域';
  } else {
    activeCropRect.value = null;
    remoteFocusMessage.value = '已退出框选模式';
  }
};

const cancelCrop = () => {
  cropMode.value = false;
  showCropCard.value = false;
  activeCropRect.value = null;
  cropDataUrl.value = '';
};

const reCrop = () => {
  showCropCard.value = false;
  activeCropRect.value = null;
  cropDataUrl.value = '';
  cropMode.value = true;
};

const copyCropImage = async () => {
  if (!cropDataUrl.value || typeof window === 'undefined') return;
  try {
    const res = await fetch(cropDataUrl.value);
    const blob = await res.blob();
    if (navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': blob })
      ]);
      isCopied.value = true;
      setTimeout(() => { isCopied.value = false; }, 2000);
      remoteFocusMessage.value = '选区截图已复制到剪贴板，可直接粘贴使用';
    }
  } catch (err) {
    console.error('Failed to copy image', err);
  }
};

const downloadCropImage = () => {
  if (!cropDataUrl.value) return;
  const link = document.createElement('a');
  link.href = cropDataUrl.value;
  link.download = `browser_crop_${new Date().toISOString().replace(/[:.]/g, '-')}.png`;
  link.click();
};

const askAiWithCrop = () => {
  if (!cropDataUrl.value) return;
  const question = (cropQuestion.value || '').trim() || '请详细分析并提取这部分截屏画面的文字、图表和关键信息。';
  emit('ask-ai-crop', {
    image: cropDataUrl.value,
    question,
  });
  showCropCard.value = false;
  activeCropRect.value = null;
  remoteFocusMessage.value = '已将选区截图发送至 AI 对话';
};
const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
);
let mobileMq: MediaQueryList | null = null;
const customWidth = ref<number | null>(null);
const isResizing = ref(false);
const BROWSER_PANEL_WIDTH_STORAGE_KEY = 'nanzi_browser_panel_width';

const syncMobile = () => {
  isMobile.value = !!mobileMq?.matches;
  if (isMobile.value) pinned.value = false;
};

const loadCustomWidth = () => {
  if (typeof window === 'undefined') return;
  const saved = localStorage.getItem(BROWSER_PANEL_WIDTH_STORAGE_KEY);
  if (!saved) return;
  const parsed = parseInt(saved, 10);
  if (!Number.isNaN(parsed) && parsed >= 360) {
    customWidth.value = parsed;
    panelWidth.value = parsed;
  }
};

const startResize = (event: MouseEvent) => {
  if (isMobile.value) return;
  event.preventDefault();
  isResizing.value = true;
  document.body.classList.add('select-none');
  window.addEventListener('mousemove', handleResizing);
  window.addEventListener('mouseup', stopResize);
};

const handleResizing = (event: MouseEvent) => {
  if (!isResizing.value) return;
  const viewportWidth = window.innerWidth;
  const minWidth = 360;
  const maxWidth = Math.min(760, Math.max(minWidth, viewportWidth - 300));
  const nextWidth = Math.min(maxWidth, Math.max(minWidth, viewportWidth - event.clientX));
  customWidth.value = nextWidth;
  panelWidth.value = nextWidth;
};

const stopResize = () => {
  if (!isResizing.value) return;
  isResizing.value = false;
  document.body.classList.remove('select-none');
  window.removeEventListener('mousemove', handleResizing);
  window.removeEventListener('mouseup', stopResize);
  if (customWidth.value) {
    localStorage.setItem(BROWSER_PANEL_WIDTH_STORAGE_KEY, String(customWidth.value));
  }
};

const resetWidth = () => {
  customWidth.value = null;
  panelWidth.value = 520;
  localStorage.removeItem(BROWSER_PANEL_WIDTH_STORAGE_KEY);
};

const panelStyle = computed(() => {
  if (isMobile.value) return {};
  const width = customWidth.value ?? panelWidth.value ?? 520;
  return {
    width: `${width}px`,
    maxWidth: 'calc(100vw - 300px)',
  };
});
const screenshotUrl = computed(() => {
  const reference = snapshot.value?.screenshot_ref;
  const snapshotId = snapshot.value?.snapshot_id;
  if (!reference || !snapshotId) return null;
  const separator = reference.includes('?') ? '&' : '?';
  return `${reference}${separator}snapshot_id=${encodeURIComponent(snapshotId)}`;
});
const loadingStage = computed(() => {
  if (props.loading && !props.sessionId) return '正在准备服务端浏览器…';
  if (props.loading && !props.viewerToken) return '正在连接浏览器服务…';
  if (!connected.value) return props.sessionId ? '正在连接实时画面…' : '等待浏览器会话…';
  if (!snapshot.value) return '正在获取页面截图…';
  return '页面已就绪';
});
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let interactionFinishTimer: ReturnType<typeof setTimeout> | null = null;
let messageResetTimer: ReturnType<typeof setTimeout> | null = null;

const setTemporaryMessage = (msg: string, durationMs = 2500) => {
  remoteFocusMessage.value = msg;
  if (messageResetTimer) clearTimeout(messageResetTimer);
  messageResetTimer = setTimeout(() => {
    if (remoteFocusMessage.value === msg) {
      remoteFocusMessage.value = '';
    }
  }, durationMs);
};

const stopPolling = () => {
  if (!pollTimer) return;
  clearInterval(pollTimer);
  pollTimer = null;
};

const stopInteractionFinishTimer = () => {
  if (!interactionFinishTimer) return;
  clearTimeout(interactionFinishTimer);
  interactionFinishTimer = null;
};

const closeSocket = () => {
  connected.value = false;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (messageResetTimer) {
    clearTimeout(messageResetTimer);
    messageResetTimer = null;
  }
  stopPolling();
  stopInteractionFinishTimer();
  interactionInProgress.value = false;
  snapshotRequestInFlight.value = false;
  if (socket.value) {
    socket.value.onclose = null;
    socket.value.close();
    socket.value = null;
  }
};

const connect = async () => {
  closeSocket();
  snapshot.value = null;
  errorMessage.value = '';
  showManualInput.value = false;
  manualText.value = '';
  controlOwner.value = 'ai';
  controlReason.value = null;
  captchaDetected.value = false;
  interactionInProgress.value = false;
  if (!props.visible || !props.sessionId || !props.viewerToken || typeof window === 'undefined') return;
  await nextTick();
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${scheme}//${window.location.host}/api/v1/chat/browser/sessions/${encodeURIComponent(props.sessionId)}/viewer`;
  const client = new WebSocket(url, [`browser-viewer.${props.viewerToken}`]);
  socket.value = client;
  client.onopen = () => {
    if (socket.value !== client) return;
    connected.value = true;
    startPolling();
  };
  client.onmessage = (event) => {
    if (socket.value !== client) return;
    let payload: {
      type?: string;
      snapshot?: BrowserSnapshot;
      tabs?: BrowserTab[];
      message?: string;
      focused_input?: boolean;
      owner?: ControlOwner;
      reason?: string | null;
      captcha?: boolean;
      detected?: boolean;
      action?: string;
      detail?: string;
    };
    try {
      payload = JSON.parse(event.data) as {
        type?: string;
        snapshot?: BrowserSnapshot;
        tabs?: BrowserTab[];
        message?: string;
        focused_input?: boolean;
        owner?: ControlOwner;
        reason?: string | null;
        captcha?: boolean;
        detected?: boolean;
        action?: string;
        detail?: string;
      };
    } catch {
      errorMessage.value = '浏览器返回了无法识别的消息';
      return;
    }
    if (payload.type === 'tabs' && Array.isArray(payload.tabs)) {
      tabs.value = payload.tabs;
    } else if (payload.type === 'control_state') {
      const prevOwner = controlOwner.value;
      controlOwner.value = payload.owner === 'human' ? 'human' : 'ai';
      controlReason.value = payload.reason || null;
      if (controlOwner.value === 'human') {
        stopPolling();
      } else {
        if (prevOwner === 'human' || remoteFocusMessage.value.includes('交还')) {
          setTemporaryMessage('✅ 已交还 AI 接管控制');
        }
        if (payload.captcha) {
          captchaDetected.value = true;
          stopPolling();
        } else if (!interactionInProgress.value && !autoRefreshPaused.value && !pollTimer) {
          startPolling();
        }
      }
    } else if (payload.type === 'captcha') {
      captchaDetected.value = Boolean(payload.detected);
      if (captchaDetected.value) {
        stopPolling();
        remoteFocusMessage.value = '检测到安全验证，请人工完成；自动刷新已暂停';
      } else if (controlOwner.value === 'human') {
        stopPolling();
      } else if (!interactionInProgress.value && !autoRefreshPaused.value && !pollTimer) {
        startPolling();
      }
    } else if (payload.type === 'focus') {
      showManualInput.value = Boolean(payload.focused_input);
      if (showManualInput.value) {
        remoteFocusMessage.value = '已聚焦输入区域，请在弹框中输入文字';
        void nextTick(() => manualInputRef.value?.focus());
      } else {
        manualText.value = '';
        remoteFocusMessage.value = '当前点击的不是输入框';
      }
    } else if (payload.type === 'ai_action') {
      if (payload.action) {
        currentAiAction.value = { action: payload.action, detail: payload.detail || '' };
      } else {
        currentAiAction.value = null;
      }
    } else if (payload.type === 'snapshot' && payload.snapshot) {
      currentAiAction.value = null;
      snapshotRequestInFlight.value = false;
      isSyncing.value = false;
      if (syncingTimer) {
        clearTimeout(syncingTimer);
        syncingTimer = null;
      }
      const previousUrl = snapshot.value?.url;
      snapshot.value = payload.snapshot;
      address.value = payload.snapshot.url || address.value;
      if (payload.snapshot.page_state === 'captcha') {
        captchaDetected.value = true;
        stopPolling();
      }
      if (currentHumanAction.value) {
        currentHumanAction.value.detail = '✅ 操作已生效';
        if (humanActionTimer) clearTimeout(humanActionTimer);
        humanActionTimer = setTimeout(() => {
          currentHumanAction.value = null;
        }, 1200);
      }
      if (previousUrl && previousUrl !== payload.snapshot.url) {
        showManualInput.value = false;
        manualText.value = '';
        setTemporaryMessage('✅ 页面已更新');
      } else if (remoteFocusMessage.value.startsWith('正在')) {
        setTemporaryMessage('✅ 操作已完成');
      }
    } else if (payload.type === 'error') {
      snapshotRequestInFlight.value = false;
      isSyncing.value = false;
      if (syncingTimer) {
        clearTimeout(syncingTimer);
        syncingTimer = null;
      }
      errorMessage.value = payload.message || '浏览器操作失败';
    }
  };
  client.onerror = () => {
    if (socket.value !== client) return;
    errorMessage.value = '浏览器连接失败，请稍后重试';
  };
  client.onclose = () => {
    if (socket.value !== client) return;
    connected.value = false;
    snapshotRequestInFlight.value = false;
    errorMessage.value = '浏览器连接已断开，正在重连…';
    if (props.visible && socket.value === client) {
      reconnectTimer = setTimeout(() => void connect(), 1500);
    }
  };
};

const send = (payload: Record<string, unknown>): boolean => {
  if (socket.value?.readyState === WebSocket.OPEN) {
    socket.value.send(JSON.stringify(payload));
    return true;
  }
  return false;
};

const requestSnapshot = () => {
  if (!connected.value) return;
  if (snapshotRequestInFlight.value) return;
  snapshotRequestInFlight.value = true;
  if (!send({ type: 'snapshot' })) {
    snapshotRequestInFlight.value = false;
  }
};

const startPolling = () => {
  stopPolling();
  if (controlOwner.value === 'human' || autoRefreshPaused.value || interactionInProgress.value || captchaDetected.value || !connected.value) return;
  pollTimer = setInterval(requestSnapshot, BROWSER_PANEL_REFRESH_INTERVAL_MS);
};

const pauseAutoRefresh = () => {
  autoRefreshPaused.value = true;
  stopPolling();
};

const resumeAutoRefresh = () => {
  autoRefreshPaused.value = false;
  if (!connected.value || captchaDetected.value || interactionInProgress.value) return;
  requestSnapshot();
  startPolling();
};

const pauseForInteraction = () => {
  interactionInProgress.value = true;
  controlOwner.value = 'human';
  stopPolling();
};

const finishInteraction = () => {
  stopInteractionFinishTimer();
  if (!interactionInProgress.value) return;
  interactionInProgress.value = false;
};

const scheduleInteractionFinish = () => {
  stopInteractionFinishTimer();
  interactionFinishTimer = setTimeout(finishInteraction, 180);
};

const releaseControl = () => {
  triggerSyncing();
  send({ type: 'release_control' });
  remoteFocusMessage.value = '正在交还 AI 控制权…';
};

const confirmCloseSession = (destroyProfile: boolean = false) => {
  showCloseSessionConfirm.value = false;
  emit('close-session', destroyProfile);
};

const remotePointFromEvent = (event: MouseEvent): RemotePoint | null => {
  const image = event.currentTarget as HTMLImageElement;
  const rect = image.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const remoteWidth = image.naturalWidth || 1280;
  const remoteHeight = image.naturalHeight || 800;
  return {
    x: ((event.clientX - rect.left) / rect.width) * remoteWidth,
    y: ((event.clientY - rect.top) / rect.height) * remoteHeight,
  };
};

let lastClickActionAt = 0;

const sendRemoteClick = (event: MouseEvent) => {
  if (isSyncing.value) {
    setTemporaryMessage('⚡ 页面响应中，请稍候…', 1200);
    return;
  }
  const now = Date.now();
  if (now - lastClickActionAt < 300) return;
  lastClickActionAt = now;

  const point = remotePointFromEvent(event);
  if (!point) return;
  addRipple(event);
  triggerSyncing();
  viewportRef.value?.focus({ preventScroll: true });

  const matched = findElementAtPoint(point);
  const targetLabel = matched
    ? `[#${matched.ref}] ${matched.role || matched.tag || '元素'}${matched.name ? ` "${matched.name}"` : ''}`
    : `坐标 (${Math.round(point.x)}, ${Math.round(point.y)})`;

  setHumanAction('click', `点击 ${targetLabel}`);
  if (!send({
    type: 'mouse_click',
    ...point,
  })) {
    errorMessage.value = '浏览器连接已断开，请等待重新连接后再操作';
    return;
  }
  remoteFocusMessage.value = '已聚焦远程页面，键盘输入将发送到当前焦点';
};

// 单击：选定元素与坐标，切换人工模式
const handleImageClick = (event: MouseEvent) => {
  if (suppressNextClick.value) {
    suppressNextClick.value = false;
    return;
  }
  const point = remotePointFromEvent(event);
  if (!point) return;
  pauseForInteraction();
  finishInteraction();
  selectedPoint.value = point;
  const matched = findElementAtPoint(point);
  selectedElement.value = matched;
  addRipple(event);
};

// 双击：在选定元素/坐标上执行真正的远程点击
const handleImageDblClick = (event: MouseEvent) => {
  if (isSyncing.value) {
    setTemporaryMessage('⚡ 页面响应中，请稍候…', 1200);
    return;
  }
  pauseForInteraction();
  sendRemoteClick(event);
  finishInteraction();
};

const suppressNativeClick = () => {
  suppressNextClick.value = true;
  if (typeof window !== 'undefined') {
    window.setTimeout(() => {
      suppressNextClick.value = false;
    }, 0);
  }
};

const releasePointerCapture = (event: PointerEvent) => {
  const image = event.currentTarget as HTMLImageElement;
  if (image.hasPointerCapture?.(event.pointerId)) {
    image.releasePointerCapture(event.pointerId);
  }
};

const handleImagePointerDown = (event: PointerEvent) => {
  if (cropMode.value) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    const image = event.currentTarget as HTMLImageElement;
    image.setPointerCapture?.(event.pointerId);
    isCropping.value = true;
    cropStartPoint.value = { x: event.clientX, y: event.clientY };
    activeCropRect.value = null;
    return;
  }

  if (event.pointerType === 'mouse' && event.button !== 0) return;
  const point = remotePointFromEvent(event);
  if (!point) return;
  const image = event.currentTarget as HTMLImageElement;
  image.setPointerCapture?.(event.pointerId);
  pointerDownPoint.value = point;
  lastPointerPoint.value = point;
  pointerDragging.value = false;
  lastPointerMoveAt = 0;
};

const handleImagePointerMove = (event: PointerEvent) => {
  const point = remotePointFromEvent(event);
  if (point) {
    cursorCoords.value = { x: Math.round(point.x), y: Math.round(point.y) };
  }

  if (cropMode.value) {
    if (!isCropping.value || !cropStartPoint.value) return;
    event.preventDefault();
    const image = event.currentTarget as HTMLImageElement;
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x1 = Math.min(cropStartPoint.value.x, event.clientX) - rect.left;
    const y1 = Math.min(cropStartPoint.value.y, event.clientY) - rect.top;
    const x2 = Math.max(cropStartPoint.value.x, event.clientX) - rect.left;
    const y2 = Math.max(cropStartPoint.value.y, event.clientY) - rect.top;
    const boundedX1 = Math.max(0, Math.min(rect.width, x1));
    const boundedY1 = Math.max(0, Math.min(rect.height, y1));
    const boundedX2 = Math.max(0, Math.min(rect.width, x2));
    const boundedY2 = Math.max(0, Math.min(rect.height, y2));
    const naturalW = image.naturalWidth || 1280;
    const naturalH = image.naturalHeight || 800;
    activeCropRect.value = {
      left: (boundedX1 / rect.width) * 100,
      top: (boundedY1 / rect.height) * 100,
      width: ((boundedX2 - boundedX1) / rect.width) * 100,
      height: ((boundedY2 - boundedY1) / rect.height) * 100,
      pixelW: ((boundedX2 - boundedX1) / rect.width) * naturalW,
      pixelH: ((boundedY2 - boundedY1) / rect.height) * naturalH,
      rectX: boundedX1,
      rectY: boundedY1,
      rectW: boundedX2 - boundedX1,
      rectH: boundedY2 - boundedY1,
    };
    return;
  }

  const start = pointerDownPoint.value;
  if (!start || !point) return;
  lastPointerPoint.value = point;
  const distance = Math.hypot(point.x - start.x, point.y - start.y);
  if (!pointerDragging.value && distance < 3) return;
  event.preventDefault();
  if (!pointerDragging.value) {
    pointerDragging.value = true;
    remoteFocusMessage.value = '正在人工拖拽远程页面';
    send({ type: 'mouse_down', ...start });
  }
  const now = Date.now();
  if (now - lastPointerMoveAt < 16) return;
  lastPointerMoveAt = now;
  send({ type: 'mouse_move', ...point });
};

const handleImagePointerUp = (event: PointerEvent) => {
  if (cropMode.value) {
    if (!isCropping.value) return;
    isCropping.value = false;
    releasePointerCapture(event);
    const image = event.currentTarget as HTMLImageElement;
    const rect = image.getBoundingClientRect();
    if (!activeCropRect.value || activeCropRect.value.rectW < 10 || activeCropRect.value.rectH < 10) {
      activeCropRect.value = null;
      return;
    }
    const naturalW = image.naturalWidth || 1280;
    const naturalH = image.naturalHeight || 800;
    const scaleX = naturalW / rect.width;
    const scaleY = naturalH / rect.height;
    const sx = activeCropRect.value.rectX * scaleX;
    const sy = activeCropRect.value.rectY * scaleY;
    const sw = activeCropRect.value.rectW * scaleX;
    const sh = activeCropRect.value.rectH * scaleY;

    const canvas = document.createElement('canvas');
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.drawImage(image, sx, sy, sw, sh, 0, 0, sw, sh);
      cropDataUrl.value = canvas.toDataURL('image/png');
      showCropCard.value = true;
      cropMode.value = false;
      cropQuestion.value = '';
      void nextTick(() => cropInputRef.value?.focus());
    }
    return;
  }

  const start = pointerDownPoint.value;
  const point = remotePointFromEvent(event) || lastPointerPoint.value;
  if (!start || !point) return;
  if (pointerDragging.value) {
    event.preventDefault();
    suppressNativeClick();
    triggerSyncing();
    setHumanAction('drag', `拖拽至 (${Math.round(point.x)}, ${Math.round(point.y)})`);
    send({ type: 'mouse_move', ...point });
    send({ type: 'mouse_up', ...point });
    remoteFocusMessage.value = '人工拖拽已发送到远程页面';
    finishInteraction();
  }
  releasePointerCapture(event);
  pointerDownPoint.value = null;
  lastPointerPoint.value = null;
  pointerDragging.value = false;
};

const handleImagePointerCancel = (event: PointerEvent) => {
  if (cropMode.value) {
    isCropping.value = false;
    releasePointerCapture(event);
    activeCropRect.value = null;
    return;
  }

  if (!pointerDownPoint.value) return;
  event.preventDefault();
  suppressNativeClick();
  if (pointerDragging.value) {
    const point = lastPointerPoint.value || pointerDownPoint.value;
    send({ type: 'mouse_up', ...point });
  }
  finishInteraction();
  releasePointerCapture(event);
  pointerDownPoint.value = null;
  lastPointerPoint.value = null;
  pointerDragging.value = false;
};

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    if (cropMode.value || showCropCard.value) {
      cancelCrop();
      return;
    }
  }

  if (event.isComposing) return;
  event.preventDefault();
  pauseForInteraction();
  triggerSyncing();
  const modifiers = [
    event.ctrlKey ? 'Control' : '',
    event.altKey ? 'Alt' : '',
    event.shiftKey ? 'Shift' : '',
    event.metaKey ? 'Meta' : '',
  ].filter(Boolean);
  const keyCombo = [...modifiers, event.key].join('+');
  setHumanAction('key', `发送按键 [${keyCombo}]`);
  send({ type: 'key', key: keyCombo });
  scheduleInteractionFinish();
};

const quickKeys = [
  { key: 'Enter', label: '↵ Enter', tip: '回车确认' },
  { key: 'Tab', label: '⇥ Tab', tip: '切换焦点' },
  { key: 'Escape', label: '⎋ Esc', tip: '取消/退出' },
  { key: 'Backspace', label: '⌫ 退格', tip: '退格删除' },
  { key: 'Space', label: '␣ 空格', tip: '空格' },
  { key: 'ArrowUp', label: '↑ 上', tip: '向上滚动/选择' },
  { key: 'ArrowDown', label: '↓ 下', tip: '向下滚动/选择' },
  { key: 'PageUp', label: '⇞ PgUp', tip: '向上翻页' },
  { key: 'PageDown', label: '⇟ PgDn', tip: '向下翻页' },
];

const sendQuickKey = (key: string, label: string) => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  setHumanAction('key', `发送按键 [${label}]`);
  send({ type: 'key', key });
  scheduleInteractionFinish();
  setTemporaryMessage(`已发送按键：${label}`, 1500);
};

const manualScroll = (direction: 'up' | 'down' | 'top' | 'bottom') => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  let deltaY = 0;
  let label = '';
  if (direction === 'top') {
    deltaY = -2000;
    label = '滚动到页面顶部';
  } else if (direction === 'bottom') {
    deltaY = 2000;
    label = '滚动到页面底部';
  } else if (direction === 'up') {
    deltaY = -520;
    label = '向上滚动页面';
  } else {
    deltaY = 520;
    label = '向下滚动页面';
  }
  setHumanAction('scroll', label);
  send({ type: 'scroll', direction, delta_y: deltaY });
  scheduleInteractionFinish();
  setTemporaryMessage(`✅ 已${label}`, 1200);
};

// 悬浮滚动控制器拖拽状态与处理
const scrollControllerRef = ref<HTMLElement | null>(null);
const floatingScrollTop = ref<number | null>(null);
const isDraggingScrollController = ref(false);

const onScrollControllerPointerDown = (event: PointerEvent) => {
  event.stopPropagation();
  event.preventDefault();
  const handleEl = event.currentTarget as HTMLElement | null;
  const controllerEl = scrollControllerRef.value;
  if (!handleEl || !controllerEl || !viewportRef.value) return;

  isDraggingScrollController.value = true;
  handleEl.setPointerCapture(event.pointerId);

  const controllerRect = controllerEl.getBoundingClientRect();
  const viewportRect = viewportRef.value.getBoundingClientRect();

  const initialTop = controllerRect.top - viewportRect.top + viewportRef.value.scrollTop;
  const startClientY = event.clientY;

  const onPointerMove = (e: PointerEvent) => {
    if (!isDraggingScrollController.value || !viewportRef.value || !scrollControllerRef.value) return;
    e.stopPropagation();
    e.preventDefault();
    const deltaY = e.clientY - startClientY;
    const viewportHeight = viewportRef.value.clientHeight;
    const controllerHeight = scrollControllerRef.value.offsetHeight;

    const minTop = 8;
    const maxTop = Math.max(minTop, viewportHeight - controllerHeight - 8);
    const calculatedTop = Math.min(Math.max(minTop, initialTop + deltaY), maxTop);

    floatingScrollTop.value = calculatedTop;
  };

  const onPointerUp = (e: PointerEvent) => {
    isDraggingScrollController.value = false;
    try {
      handleEl.releasePointerCapture(e.pointerId);
    } catch {}
    handleEl.removeEventListener('pointermove', onPointerMove);
    handleEl.removeEventListener('pointerup', onPointerUp);
    handleEl.removeEventListener('pointercancel', onPointerUp);
  };

  handleEl.addEventListener('pointermove', onPointerMove);
  handleEl.addEventListener('pointerup', onPointerUp);
  handleEl.addEventListener('pointercancel', onPointerUp);
};

const sendText = () => {
  if (!manualText.value) return;
  if (isSyncing.value) return;
  if (!showManualInput.value) {
    remoteFocusMessage.value = '尚未点击远程输入区域，请先点击截图中的搜索框';
    return;
  }
  pauseForInteraction();
  triggerSyncing();
  setHumanAction('text', `发送文字「${manualText.value.slice(0, 12)}${manualText.value.length > 12 ? '…' : ''}」`);
  send({ type: 'text', text: manualText.value });
  remoteFocusMessage.value = '文字已发送到远程页面';
  manualText.value = '';
  finishInteraction();
};

const normalizeNavigationUrl = (raw: string) => {
  const value = raw.trim();
  if (!value) return '';
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith('//')) return `https:${value}`;
  if (/^[a-z][a-z\d+.-]*:/i.test(value)) return value;
  return `https://${value}`;
};

const navigate = () => {
  if (isSyncing.value) return;
  const value = normalizeNavigationUrl(address.value);
  if (!value) return;
  address.value = value;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('navigate', `访问 ${value.slice(0, 25)}…`);
  remoteFocusMessage.value = '页面导航中，请等待加载后重新点击输入区域';
  send({ type: 'navigate', url: value });
  finishInteraction();
};

const goBack = () => {
  if (isSyncing.value || !snapshot.value?.can_go_back) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('navigate', '后退至上一页');
  remoteFocusMessage.value = '正在后退至上一页…';
  send({ type: 'go_back' });
  finishInteraction();
};

const goForward = () => {
  if (isSyncing.value || !snapshot.value?.can_go_forward) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('navigate', '前进至下一页');
  remoteFocusMessage.value = '正在前进至下一页…';
  send({ type: 'go_forward' });
  finishInteraction();
};

const reloadPage = () => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('navigate', '重新加载页面');
  remoteFocusMessage.value = '正在重新加载页面…';
  send({ type: 'reload' });
  finishInteraction();
};

const switchTab = (tabId: string) => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '切换标签页');
  remoteFocusMessage.value = '正在切换标签页…';
  send({ type: 'switch_tab', tab_id: tabId });
  finishInteraction();
};

const closeTab = (tabId: string) => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '关闭标签页');
  remoteFocusMessage.value = '正在关闭标签页…';
  send({ type: 'close_tab', tab_id: tabId });
  finishInteraction();
};

const newTab = () => {
  if (isSyncing.value) return;
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '新建标签页');
  remoteFocusMessage.value = '正在新建标签页…';
  send({ type: 'new_tab', url: 'https://www.baidu.com' });
  finishInteraction();
};

const closeOtherTabs = (tabId: string) => {
  if (isSyncing.value) return;
  closeTabContextMenu();
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '关闭其他标签页');
  remoteFocusMessage.value = '正在关闭其他标签页…';
  send({ type: 'close_other_tabs', tab_id: tabId });
  finishInteraction();
};

const closeTabsToRight = (tabId: string) => {
  if (isSyncing.value) return;
  closeTabContextMenu();
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '关闭右侧标签页');
  remoteFocusMessage.value = '正在关闭右侧标签页…';
  send({ type: 'close_tabs_to_right', tab_id: tabId });
  finishInteraction();
};

const closeAllTabs = () => {
  if (isSyncing.value) return;
  closeTabContextMenu();
  pauseForInteraction();
  triggerSyncing();
  showManualInput.value = false;
  manualText.value = '';
  setHumanAction('tab', '关闭所有标签页');
  remoteFocusMessage.value = '正在关闭所有标签页并重置…';
  send({ type: 'close_all_tabs' });
  finishInteraction();
};

watch(
  () => [props.visible, props.sessionId, props.viewerToken] as const,
  () => void connect(),
  { immediate: true },
);

watch(
  () => props.visible,
  (visible) => {
    showCloseSessionConfirm.value = false;
    showManualInput.value = false;
    manualText.value = '';
    closeTabContextMenu();
    controlOwner.value = 'ai';
    controlReason.value = null;
    captchaDetected.value = false;
    interactionInProgress.value = false;
    autoRefreshPaused.value = false;
    if (visible && !isMobile.value) pinned.value = true;
  },
);

watch(() => props.approvalMode,
  (mode, previous) => {
    if (mode === 'guarded' && previous !== 'guarded') {
      showSafetyNotice.value = true;
    }
  },
);

watch(() => props.refreshSignal, () => {
  if (!props.visible || !connected.value) return;
  if (controlOwner.value === 'human' || autoRefreshPaused.value || captchaDetected.value) return;
  requestSnapshot();
});

onMounted(() => {
  loadCustomWidth();
  void fetchEnvironmentInfo();
  void fetchPanelCacheSize();
  mobileMq = window.matchMedia('(max-width: 639px)');
  syncMobile();
  mobileMq.addEventListener?.('change', syncMobile);
  window.addEventListener('resize', syncMobile);
  window.addEventListener('click', closeTabContextMenu);
});

onUnmounted(() => {
  stopResize();
  closeSocket();
  mobileMq?.removeEventListener?.('change', syncMobile);
  window.removeEventListener('resize', syncMobile);
  window.removeEventListener('click', closeTabContextMenu);
});
</script>

<style scoped>
.fade-capsule-enter-active,
.fade-capsule-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.fade-capsule-enter-from,
.fade-capsule-leave-to {
  opacity: 0;
  transform: translateY(4px) scale(0.95);
}
</style>
