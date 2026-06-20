<template>
  <section ref="menuContainer" class="space-y-4">
    <!-- 状态条 -->
    <div
      v-if="showStatusBanner"
      class="rounded-xl border px-3 py-2 text-[11px] leading-relaxed flex items-start gap-2"
      :class="statusBannerClass"
    >
      <svg v-if="portalStatus === 'loading'" class="w-4 h-4 animate-spin flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span v-else class="flex-shrink-0">{{ statusBannerIcon }}</span>
      <span>{{ statusBannerText }}</span>
    </div>

    <!-- Header -->
    <div class="bg-gray-50/40 dark:bg-gray-800/10 backdrop-blur-sm border border-gray-150 dark:border-gray-800/80 rounded-xl p-3 flex flex-row items-center justify-between gap-2">
      <div class="flex items-center gap-2.5 min-w-0">
        <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-600 dark:bg-blue-500 text-white shadow-sm flex-shrink-0">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z"/>
          </svg>
        </div>
        <div class="min-w-0">
          <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100 tracking-wide">我的数据门户</h3>
          <div class="flex flex-wrap items-center gap-1.5 mt-0.5 text-[10px]">
            <span
              v-if="payload.dataset_count !== undefined"
              class="font-semibold text-blue-600 dark:text-blue-400"
            >
              {{ payload.dataset_count }} 个数据集
            </span>
            <template v-if="payload.generated_at">
              <span class="text-gray-300 dark:text-gray-700">|</span>
              <span class="text-gray-500 dark:text-gray-400">更新 {{ formattedGeneratedAt }}</span>
            </template>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-1.5 flex-shrink-0">
        <!-- 🔍 搜索折叠切换按钮 -->
        <button
          type="button"
          class="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent transition-all cursor-pointer active:scale-90"
          :class="showSearchBar
            ? 'bg-blue-55 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400 font-bold'
            : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400 hover:bg-gray-200/70 dark:hover:bg-gray-750 hover:text-gray-700 dark:hover:text-gray-200'"
          title="展开/折叠搜索与标签过滤"
          @click="showSearchBar = !showSearchBar"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </button>

        <!-- 刷新按钮 -->
        <button
          type="button"
          class="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent bg-gray-100 text-gray-550 dark:bg-gray-800 dark:text-gray-400 transition-all"
          :class="refreshDisabled
            ? 'opacity-50 cursor-not-allowed'
            : 'hover:bg-gray-200/70 dark:hover:bg-gray-750 hover:text-gray-700 dark:hover:text-gray-200 active:scale-90 cursor-pointer'"
          :disabled="refreshDisabled"
          :title="refreshDisabled && props.initialLoading ? '数据门户首次加载中，请稍候' : refreshButtonTitle"
          @click="handleRefreshClick"
        >
          <svg 
            class="w-4 h-4 transition-transform duration-700"
            :class="{ 'animate-spin': showRefreshBusy }"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Search and Filter Bar -->
    <transition
      enter-active-class="transition-all duration-300 ease-out"
      enter-from-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
      enter-to-class="transform opacity-100 translate-y-0 max-h-[120px] overflow-hidden"
      leave-active-class="transition-all duration-200 ease-in"
      leave-from-class="transform opacity-100 translate-y-0 max-h-[120px] overflow-hidden"
      leave-to-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
    >
      <div v-show="showSearchBar && !isNoPermissionEmpty" class="space-y-2.5">
        <div class="relative">
          <span class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none text-gray-400 dark:text-gray-500">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索场景、表名或指标..."
            class="w-full pl-9 pr-8 py-1.5 text-xs rounded-xl border border-gray-150 dark:border-gray-800 bg-white dark:bg-gray-900/30 text-gray-850 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all shadow-xs"
          />
          <button
            v-if="searchQuery"
            type="button"
            class="absolute inset-y-0 right-0 flex items-center pr-2.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
            @click="searchQuery = ''"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div v-if="allTags.length" class="relative">
          <div class="pointer-events-none absolute inset-y-0 left-0 w-4 bg-gradient-to-r from-white dark:from-gray-900 to-transparent z-10 sm:hidden"></div>
          <div class="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-white dark:from-gray-900 to-transparent z-10 sm:hidden"></div>
          <div class="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1 -mx-0.5 scroll-smooth">
            <button
              type="button"
              class="px-2.5 py-1 text-[10px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95"
              :class="selectedTag === 'All'
                ? 'bg-blue-600 border-transparent text-white shadow-xs'
                : 'bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
              @click="selectedTag = 'All'"
            >
              全部
            </button>
            <button
              v-for="tag in allTags"
              :key="tag"
              type="button"
              class="px-2.5 py-1 text-[10px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95"
              :class="tagFilterClass(tag)"
              @click="selectedTag = tag"
            >
              {{ tag }}
            </button>
          </div>
        </div>
      </div>
    </transition>
    <!-- 📌 我的黄金报表 (Redis 暂存版) -->
    <div
      v-if="!initialLoading"
      class="rounded-xl border border-blue-150/70 dark:border-blue-900/40 bg-blue-50/10 dark:bg-blue-950/5 p-3.5 space-y-2.5 animate-fade-in-up"
    >
      <div
        class="flex items-center justify-between w-full text-[11px] font-bold text-blue-700 dark:text-blue-400 uppercase tracking-wider transition-colors select-none cursor-pointer"
        @click="showSavedReportsCollapse = !showSavedReportsCollapse; if (!showSavedReportsCollapse) fetchSavedReports();"
      >
        <span class="flex items-center gap-1.5">
          <span>📌</span> 我的黄金报表
          <span 
            v-if="savedReports.length > 0"
            class="rounded-full px-1.5 py-0.5 text-[9px] font-bold bg-blue-100 dark:bg-blue-900/60 text-blue-700 dark:text-blue-355"
          >
            {{ savedReports.length }} 个
          </span>
        </span>
        <div class="flex items-center space-x-2">
          <button
            type="button"
            class="p-1 rounded hover:bg-blue-100/80 dark:hover:bg-blue-900/40 text-blue-700 dark:text-blue-400 transition-colors cursor-pointer flex items-center justify-center"
            title="刷新黄金报表"
            @click.stop="fetchSavedReports"
          >
            <svg
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': loadingReports }"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <svg
            class="w-3.5 h-3.5 transform transition-transform duration-300 pointer-events-none"
            :class="{ 'rotate-180': !showSavedReportsCollapse }"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      <div 
        v-if="!showSavedReportsCollapse"
        class="space-y-2"
      >
        <div v-if="loadingReports" class="flex items-center justify-center py-4 text-xs text-gray-400 select-none">
          <svg class="w-4 h-4 animate-spin text-blue-500 mr-2" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          正在加载暂存报表...
        </div>
        <div v-else-if="savedReports.length === 0" class="text-center py-6 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl text-gray-400 dark:text-gray-500 text-[11px] select-none">
          暂无已存报表。您可以在 ChatBI 结果中点击 SQL 上方的“暂存”按钮进行快捷收藏。
        </div>
        <div v-else class="grid gap-2 max-h-60 overflow-y-auto pr-0.5 custom-scrollbar">
          <div
            v-for="report in savedReports"
            :key="report.id"
            class="group/item flex items-stretch rounded-lg border border-blue-100/60 dark:border-blue-900/30 bg-white dark:bg-gray-900/40 hover:border-blue-300 dark:hover:border-blue-700/60 overflow-hidden shadow-xs"
          >
            <button
              type="button"
              class="flex-1 flex flex-col p-2.5 text-left transition-all active:scale-[0.99] hover:bg-blue-50/30 dark:hover:bg-blue-950/20 min-w-0"
              @click="handleExecuteSavedReportClick(report)"
            >
              <span class="text-xs font-bold text-gray-800 dark:text-gray-200 truncate w-full" :title="report.title">
                {{ report.title }}
              </span>
              <span v-if="report.original_query" class="text-[10px] text-gray-400 dark:text-gray-500 truncate mt-1 w-full" :title="report.original_query">
                问: {{ report.original_query }}
              </span>
              <span class="text-[9px] text-gray-400 dark:text-gray-550 mt-1 select-none font-mono">
                {{ formatDate(report.created_at) }}
              </span>
            </button>
            <button
              type="button"
              class="flex items-center justify-center w-8 text-gray-400 hover:text-red-500 border-l border-blue-50/60 dark:border-blue-900/20 hover:bg-red-50 dark:hover:bg-red-950/40 transition-colors cursor-pointer"
              title="删除暂存"
              @click.stop="handleDeleteReport(report)"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 我常问 -->
    <div
      v-if="showFrequentSection"
      class="rounded-xl border border-amber-100/80 dark:border-amber-900/40 bg-amber-50/10 dark:bg-amber-950/5 p-3.5 space-y-2.5 animate-fade-in-up"
    >
      <!-- Header：折叠 + 计数，对齐黄金报表样式 -->
      <div
        class="flex items-center justify-between w-full text-[11px] font-bold text-amber-700 dark:text-amber-400 uppercase tracking-wider transition-colors select-none cursor-pointer"
        @click="showFrequentCollapse = !showFrequentCollapse"
      >
        <span class="flex items-center gap-1.5">
          <span>🔥</span> 我常问
          <span
            class="rounded-full px-1.5 py-0.5 text-[9px] font-bold bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300"
          >
            {{ frequentQuestions.length }} 个
          </span>
        </span>
        <svg
          class="w-3.5 h-3.5 transform transition-transform duration-300 pointer-events-none"
          :class="{ 'rotate-180': !showFrequentCollapse }"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      <!-- 内容区 -->
      <div v-if="!showFrequentCollapse" class="flex flex-wrap gap-2">
        <div
          v-for="item in frequentQuestions"
          :key="item.question.query"
          class="group/freq inline-flex items-stretch rounded-lg border border-amber-200/70 dark:border-amber-800/50 bg-white/80 dark:bg-gray-900/40 overflow-hidden"
        >
          <button
            type="button"
            class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] font-semibold text-amber-900 dark:text-amber-100 hover:bg-amber-50 dark:hover:bg-amber-950/40 transition-all active:scale-95 cursor-pointer"
            @click="handleFrequentQuestionClick(item, 'send')"
          >
            <span class="truncate max-w-[200px] sm:max-w-[220px]">{{ item.question.label }}</span>
            <span class="text-[9px] font-bold text-amber-600 dark:text-amber-400">{{ item.question.click_count }}次</span>
          </button>
          <button
            type="button"
            class="opacity-0 group-hover/freq:opacity-100 flex items-center justify-center px-1.5 text-amber-500/80 dark:text-amber-400/80 border-l border-amber-200/70 dark:border-amber-800/50 hover:bg-amber-100/80 dark:hover:bg-amber-950/60 hover:text-amber-700 dark:hover:text-amber-200 transition-all duration-200 cursor-pointer"
            title="填入输入框微调"
            aria-label="填入输入框微调"
            @click.stop="handleFrequentQuestionClick(item, 'fill')"
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.83 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
            </svg>
          </button>
          <button
            type="button"
            class="flex items-center justify-center px-1.5 text-amber-500/80 dark:text-amber-400/80 border-l border-amber-200/70 dark:border-amber-800/50 hover:bg-amber-100/80 dark:hover:bg-amber-950/60 hover:text-amber-700 dark:hover:text-amber-200 transition-colors cursor-pointer"
            title="从常问中移除"
            aria-label="从常问中移除"
            @click.stop="handleClearFrequentQuestion(item)"
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 首屏骨架 -->
    <div v-if="initialLoading" class="grid gap-4">
      <div v-for="i in 3" :key="`sk-${i}`" class="portal-skeleton-card rounded-xl border border-gray-150 dark:border-gray-800 p-4 space-y-3">
        <div class="flex items-center gap-2">
          <div class="portal-skeleton-shimmer w-9 h-9 rounded-xl"></div>
          <div class="portal-skeleton-shimmer h-4 w-32 rounded"></div>
        </div>
        <div class="portal-skeleton-shimmer h-12 w-full rounded-lg"></div>
        <div class="flex gap-2">
          <div class="portal-skeleton-shimmer h-8 w-24 rounded-lg"></div>
          <div class="portal-skeleton-shimmer h-8 w-28 rounded-lg"></div>
        </div>
      </div>
    </div>

    <!-- Cards Grid Container with Loading Overlay -->
    <div v-else class="relative">
      <!-- Loading Overlay -->
      <transition
        enter-active-class="transition-opacity duration-200"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="transition-opacity duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div 
          v-if="showRefreshBusy" 
          class="absolute inset-0 bg-white/60 dark:bg-gray-900/60 backdrop-blur-[2px] z-20 rounded-xl flex items-start justify-center pt-24 pointer-events-auto"
        >
          <div class="flex flex-col items-center gap-2">
            <svg class="w-8 h-8 animate-spin text-blue-600 dark:text-blue-500" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-xs font-bold text-gray-500 dark:text-gray-400 select-none animate-pulse">正在刷新数据门户...</span>
          </div>
        </div>
      </transition>

      <div class="grid gap-4" :class="{ 'pointer-events-none select-none': showRefreshBusy }">
        <article
          v-for="{ group, visuals } in visibleDisplayGroups"
          :key="group.id || group.title"
          class="group/card relative overflow-hidden rounded-xl border p-3.5 sm:p-4 shadow-sm hover:shadow-lg hover:-translate-y-0.5 transition-all duration-300"
          :class="[
            visuals.card, visuals.cardBorder, visuals.cardHover,
            canDragCards ? 'cursor-grab active:cursor-grabbing' : '',
            dragOverId === (group.id || group.title) ? 'ring-2 ring-blue-400/60 dark:ring-blue-500/60 scale-[1.01]' : '',
            dragSourceId === (group.id || group.title) ? 'opacity-50' : '',
          ]"
          :draggable="canDragCards"
          @dragstart="canDragCards && handleDragStart($event, group.id || group.title)"
          @dragover="handleDragOver($event, group.id || group.title)"
          @dragleave="handleDragLeave($event)"
          @drop="handleDrop($event, group.id || group.title)"
          @dragend="handleDragEnd()"
        >
          <!-- 拖拽 Handle 提示（无筛选时悬停显示） -->
          <div
            v-if="canDragCards"
            class="absolute top-2 left-1/2 -translate-x-1/2 opacity-0 group-hover/card:opacity-40 transition-opacity duration-200 pointer-events-none select-none"
          >
            <svg class="w-4 h-3 text-gray-500 dark:text-gray-400" viewBox="0 0 16 10" fill="currentColor">
              <circle cx="4" cy="2" r="1.2"/><circle cx="8" cy="2" r="1.2"/><circle cx="12" cy="2" r="1.2"/>
              <circle cx="4" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="12" cy="8" r="1.2"/>
            </svg>
          </div>
          <div
            class="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-[0.18] blur-2xl transition-opacity duration-300 group-hover/card:opacity-[0.28]"
            :class="visuals.decor"
          />
          <div
            class="pointer-events-none absolute -left-6 bottom-0 h-20 w-20 rounded-full opacity-[0.08] blur-xl"
            :class="visuals.decor"
          />

          <!-- Card Header -->

          <div class="relative space-y-2.5">
            <div class="flex items-start gap-2.5 min-w-0">
              <div
                class="portal-card-icon flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-xl shadow-sm border text-white"
                :class="visuals.iconBorder"
                :data-theme="visuals.key"
                v-html="visuals.icon"
              />
              <h4
                class="flex-1 min-w-0 text-sm font-bold leading-snug break-words pt-0.5"
                :class="visuals.title"
                :title="group.title"
              >
                {{ group.title }}
              </h4>
              <!-- 置顶按钮 -->
              <button
                type="button"
                class="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-lg transition-all duration-200 active:scale-90 mt-0.5"
                :class="isPinned(group)
                  ? 'text-amber-500 bg-amber-50 dark:bg-amber-900/30 opacity-100'
                  : 'text-gray-300 dark:text-gray-600 opacity-0 group-hover/card:opacity-100 hover:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-900/20'"
                :title="isPinned(group) ? '取消置顶' : '置顶此卡片'"
                :aria-pressed="isPinned(group)"
                @click.stop="togglePinGroup($event, group)"
              >
                <!-- 已置顶：实心图钉 -->
                <svg v-if="isPinned(group)" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"/>
                </svg>
                <!-- 未置顶：线框图钉 -->
                <svg v-else class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="12" y1="17" x2="12" y2="22"/>
                  <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/>
                </svg>
              </button>
            </div>

            <div
              v-if="group.tags?.length"
              class="flex flex-wrap gap-1 pl-11 min-w-0"
            >
              <span
                v-for="tag in group.tags.slice(0, 3)"
                :key="tag"
                class="inline-block max-w-full rounded-full border px-2 py-0.5 text-[9px] font-bold leading-tight line-clamp-2 break-all"
                :class="visuals.tag"
                :title="tag"
              >
                {{ formatTagLabel(tag) }}
              </span>
            </div>

            <blockquote
              v-if="group.summary"
              class="portal-card-summary relative w-full m-0 px-3.5 py-2.5 text-xs leading-relaxed rounded-r-lg border-l-[3px]"
              :class="visuals.quote"
              :style="{ '--summary-strong': visuals.strongColor }"
              v-html="formatGroupSummary(group.summary)"
            />

            <div v-if="group.metrics?.length" class="relative mt-2.5">
              <div class="mb-1.5 text-[10px] font-bold uppercase tracking-wider flex items-center gap-1 select-none" :class="visuals.sectionLabel">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
                </svg>
                核心指标
              </div>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="metric in group.metrics"
                  :key="`${group.id || group.title}-${metric}`"
                  type="button"
                  class="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold rounded-lg border transition-all active:scale-95"
                  :class="visuals.questionBtn"
                  :title="`点击查询「${metric}」趋势`"
                  @click.stop="handleMetricClick(metric, group)"
                >
                  <span>{{ metric }}</span>
                  <svg class="w-2.5 h-2.5 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M9 5l7 7-7 7"/>
                  </svg>
                </button>
              </div>
            </div>
          </div>

          <!-- You Can Ask Section -->
          <div v-if="group.questions?.length" class="relative mt-4">
            <div class="mb-2 flex items-center justify-between select-none">
              <span
                class="text-[10px] font-bold uppercase tracking-wider flex items-center gap-1"
                :class="visuals.sectionLabel"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
                你可以这样问
                <span
                  class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full text-[9px] font-bold text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 cursor-help border border-gray-300/70 dark:border-gray-600/70"
                  :title="QUESTIONS_SECTION_TIP"
                  aria-label="你可以这样问说明"
                  @click.stop
                >?</span>
              </span>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-[10px] font-bold transition-all duration-200 cursor-pointer active:scale-95 px-2 py-0.5 rounded-md border disabled:opacity-50"
                :class="visuals.refreshBtn"
                :disabled="isQuestionsRefreshDisabled(group)"
                @click.stop="handleRefreshGroupQuestions(group)"
              >
                <svg
                  class="w-3 h-3 opacity-70"
                  :class="{ 'animate-spin': refreshingGroupIds[group.id || group.title] }"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                </svg>
                <span>换一批</span>
              </button>
            </div>

            <div v-if="refreshingGroupIds[group.id || group.title]" class="flex flex-wrap gap-2 animate-pulse-slow">
              <div
                v-for="i in 3"
                :key="i"
                class="inline-flex items-center h-8 w-32 rounded-lg border"
                :class="visuals.questionSkeleton"
              ></div>
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <div
                v-for="question in sortedQuestions(group.questions)"
                :key="question.query"
                class="group/qbtn inline-flex items-stretch rounded-lg border transition-all duration-200 hover:-translate-y-0.5 active:translate-y-0 shadow-sm overflow-hidden"
                :class="visuals.questionBtn"
              >
                <button
                  type="button"
                  class="inline-flex items-center gap-1.5 px-3 py-2 text-left text-xs font-semibold cursor-pointer select-none bg-transparent hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
                  @click="handleQuestionClick(question, group, 'send')"
                >
                  <svg
                    class="w-3.5 h-3.5 flex-shrink-0 opacity-80"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                  </svg>
                  <span>{{ question.label }}</span>
                  <span
                    v-if="question.click_count"
                    class="ml-1 inline-flex items-center px-1 py-0.5 rounded bg-amber-500/10 text-[9px] font-bold text-amber-600 border border-amber-500/20 dark:bg-amber-500/20 dark:text-amber-300 dark:border-amber-500/30 shadow-sm"
                  >
                    🔥 常用 {{ question.click_count }}
                  </span>
                </button>
                <button
                  type="button"
                  class="opacity-0 group-hover/qbtn:opacity-100 flex items-center justify-center px-2 border-l transition-all duration-200 cursor-pointer bg-transparent hover:bg-black/5 dark:hover:bg-white/5"
                  :class="[
                    visuals.key === 'blue' ? 'border-blue-200/80 dark:border-blue-800/50' :
                    visuals.key === 'violet' ? 'border-violet-200/80 dark:border-violet-800/50' :
                    visuals.key === 'emerald' ? 'border-emerald-200/80 dark:border-emerald-800/50' :
                    visuals.key === 'amber' ? 'border-amber-200/80 dark:border-amber-800/50' :
                    visuals.key === 'rose' ? 'border-rose-200/80 dark:border-rose-800/50' :
                    'border-cyan-200/80 dark:border-cyan-800/50'
                  ]"
                  title="填入输入框微调"
                  aria-label="填入输入框微调"
                  @click.stop="handleQuestionClick(question, group, 'fill')"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.83 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
                  </svg>
                </button>
              </div>
            </div>
          </div>


          <!-- Related Data Section -->
          <div
            v-if="group.related_data?.length"
            class="relative mt-4 border-t pt-3"
            :class="visuals.divider"
          >
            <button
              type="button"
              class="flex items-center justify-between w-full text-left text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider hover:text-gray-600 dark:hover:text-gray-300 transition-colors select-none"
              @click="toggleGroup(group.id || group.title)"
            >
              <span class="flex items-center gap-1.5">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
                </svg>
                相关数据
                <span class="rounded-full px-1.5 py-0.5 text-[9px] font-bold bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 normal-case tracking-normal">
                  {{ countRelatedTables(group) }} 张表
                </span>
              </span>
              <svg
                class="w-3.5 h-3.5 transform transition-transform duration-300"
                :class="{ 'rotate-180': expandedGroups[group.id || group.title] }"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            
            <div 
              class="grid transition-all duration-300 ease-in-out"
              :class="expandedGroups[group.id || group.title] ? 'grid-rows-[1fr] opacity-100 mt-2.5 overflow-visible' : 'grid-rows-[0fr] opacity-0 overflow-hidden'"
            >
              <div class="min-h-0 overflow-visible">
                <div class="space-y-3 bg-gray-50/50 dark:bg-gray-950/20 rounded-xl p-3 border border-gray-100 dark:border-gray-800">
                  <div v-for="related in group.related_data" :key="related.dataset || related.display_name" class="space-y-1.5">
                    <div class="text-[11px] font-bold text-gray-500 dark:text-gray-400 flex items-center gap-1 select-none">
                      <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2z"/>
                      </svg>
                      {{ related.display_name || related.dataset }}
                    </div>
                    <div class="flex flex-wrap gap-1.5 overflow-visible">
                      <div
                        v-for="table in related.tables || []"
                        :key="table"
                        class="relative inline-block overflow-visible"
                      >
                        <span
                          class="inline-flex items-center gap-1 rounded bg-white dark:bg-gray-800 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:text-gray-300 ring-1 ring-gray-100 dark:ring-gray-700/60 shadow-sm hover:shadow-xs transition-all duration-200 cursor-pointer select-none active:scale-95"
                          :class="pinnedTableDictionary === buildTableDictionaryKey(group, related, table) ? 'ring-2 ring-primary/35 text-primary dark:text-primary' : 'hover:scale-102'"
                          @click.stop="handleTableDictionaryClick($event, group, related, table)"
                        >
                          <svg class="w-2.5 h-2.5 text-gray-400 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                          </svg>
                          {{ table }}
                        </span>

                        <!-- Popover Data Dictionary -->
                        <transition
                          enter-active-class="transition-all duration-200 ease-out"
                          enter-from-class="opacity-0 translate-y-1 scale-95"
                          enter-to-class="opacity-100 translate-y-0 scale-100"
                          leave-active-class="transition-all duration-150 ease-in"
                          leave-from-class="opacity-100 translate-y-0 scale-100"
                          leave-to-class="opacity-0 translate-y-1 scale-95"
                        >
                          <div 
                            v-if="pinnedTableDictionary === buildTableDictionaryKey(group, related, table) && related.table_columns?.[table]?.length" 
                            class="absolute z-45 bottom-full mb-2 w-72 bg-white dark:bg-gray-850 border border-gray-150 dark:border-gray-750 rounded-xl p-3 shadow-xl text-left select-none ring-1 ring-black/5"
                            :style="popoverStyles[buildTableDictionaryKey(group, related, table)] || { left: '50%', transform: 'translateX(-50%)' }"
                            @click.stop
                          >
                            <h5 class="text-[10px] font-bold text-gray-800 dark:text-gray-250 mb-2 border-b border-gray-100 dark:border-gray-750 pb-1 flex items-center justify-between">
                              <span class="flex items-center gap-1 truncate">
                                <svg class="w-3.5 h-3.5 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
                                </svg>
                                <span class="truncate">{{ table }} 字段字典</span>
                              </span>
                              <button 
                                type="button" 
                                class="text-gray-400 hover:text-gray-650 dark:hover:text-gray-200 w-4 h-4 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors flex-shrink-0 cursor-pointer"
                                @click.stop="pinnedTableDictionary = null"
                              >
                                &times;
                              </button>
                            </h5>
                            <div class="max-h-48 overflow-y-auto no-scrollbar space-y-2 pr-0.5">
                              <div 
                                v-for="col in related.table_columns[table]" 
                                :key="col.name" 
                                class="flex flex-col border-b border-gray-50/50 dark:border-gray-750/30 pb-1.5 last:border-0 last:pb-0"
                              >
                                <div class="flex items-center justify-between gap-2">
                                  <span class="font-mono text-[9px] font-bold text-blue-600 dark:text-blue-400 truncate">{{ col.name }}</span>
                                  <span class="text-[8px] bg-gray-100 dark:bg-gray-700/80 px-1 rounded text-gray-500 dark:text-gray-400 font-bold uppercase">{{ col.type }}</span>
                                </div>
                                <div class="text-[9px] text-gray-500 dark:text-gray-450 mt-0.5 font-medium leading-normal">
                                  {{ col.term }}
                                  <span v-if="col.description && col.description !== col.term" class="text-gray-400 dark:text-gray-500 ml-1 text-[8.5px] font-normal">
                                    ({{ col.description }})
                                  </span>
                                </div>
                              </div>
                            </div>
                            <!-- 快捷提问按钮组 -->
                            <div class="mt-2.5 pt-2.5 border-t border-gray-100 dark:border-gray-750 flex items-center justify-between gap-1">
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-blue-50 hover:bg-blue-100 dark:bg-blue-950/30 dark:hover:bg-blue-900/40 text-blue-600 dark:text-blue-400 border border-blue-100/50 dark:border-blue-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap"
                                title="分析表结构和字段口径"
                                @click.stop="handleQuickQuestionClick('structure', table, related)"
                              >
                                <span>📖 结构说明</span>
                              </button>
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/30 dark:hover:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400 border border-emerald-100/50 dark:border-emerald-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap"
                                title="查询最近100条明细数据"
                                @click.stop="handleQuickQuestionClick('query', table, related)"
                              >
                                <span>📊 查询明细</span>
                              </button>
                              <button
                                type="button"
                                class="flex-1 flex items-center justify-center gap-0.5 py-1 px-1.5 text-[9px] font-bold rounded bg-amber-50 hover:bg-amber-100 dark:bg-amber-950/30 dark:hover:bg-amber-900/40 text-amber-600 dark:text-amber-400 border border-amber-100/50 dark:border-amber-900/30 transition-all cursor-pointer active:scale-95 whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed"
                                title="基于字段定义推荐业务分析问题"
                                :disabled="tableRecommendState[buildTableDictionaryKey(group, related, table)]?.loading"
                                @click.stop="handleRecommendQuestions(group, related, table)"
                              >
                                <span v-if="tableRecommendState[buildTableDictionaryKey(group, related, table)]?.loading">生成中…</span>
                                <span v-else>💡 推荐提问</span>
                              </button>
                            </div>
                            <div
                              v-if="tableRecommendState[buildTableDictionaryKey(group, related, table)]"
                              class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-750 space-y-1.5"
                            >
                              <div
                                v-if="tableRecommendState[buildTableDictionaryKey(group, related, table)]?.loading"
                                class="flex items-center gap-1.5 text-[9px] text-amber-700/80 dark:text-amber-300/80"
                              >
                                <svg class="w-3 h-3 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
                                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                正在基于字段定义生成推荐提问…
                              </div>
                              <div
                                v-else-if="tableRecommendState[buildTableDictionaryKey(group, related, table)]?.error"
                                class="text-[9px] text-rose-600 dark:text-rose-400 leading-relaxed"
                              >
                                {{ tableRecommendState[buildTableDictionaryKey(group, related, table)]?.error }}
                              </div>
                              <template v-else>
                                <div class="text-[9px] font-bold text-gray-500 dark:text-gray-400">推荐业务提问</div>
                                <div
                                  v-for="question in tableRecommendState[buildTableDictionaryKey(group, related, table)]?.questions || []"
                                  :key="question.query"
                                  class="group/rec w-full inline-flex items-stretch rounded-lg border border-amber-100/80 dark:border-amber-900/40 bg-amber-50/50 dark:bg-amber-950/20 text-[9px] font-semibold text-amber-900 dark:text-amber-100 hover:bg-amber-100/70 dark:hover:bg-amber-950/40 transition-all overflow-hidden"
                                >
                                  <button
                                    type="button"
                                    class="flex-1 text-left px-2 py-1.5 bg-transparent hover:bg-black/5 dark:hover:bg-white/5 transition-colors cursor-pointer"
                                    @click.stop="handleRecommendedQuestionClick(question, 'send')"
                                  >
                                    🙋 {{ question.label }}
                                  </button>
                                  <button
                                    type="button"
                                    class="opacity-0 group-hover/rec:opacity-100 flex items-center justify-center px-2 border-l border-amber-100/80 dark:border-amber-900/40 transition-all duration-200 cursor-pointer bg-transparent hover:bg-black/5 dark:hover:bg-white/5"
                                    title="填入输入框微调"
                                    aria-label="填入输入框微调"
                                    @click.stop="handleRecommendedQuestionClick(question, 'fill')"
                                  >
                                    <svg class="w-3 h-3 text-amber-500/80 dark:text-amber-400/80" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                                      <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.83 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
                                    </svg>
                                  </button>
                                </div>
                              </template>
                            </div>
                          </div>
                        </transition>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Follow-ups Section -->
          <div v-if="group.followups?.length" class="mt-4 border-t border-gray-100 dark:border-gray-800/80 pt-3">
            <div class="mb-2 flex items-center justify-between select-none">
              <span class="text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-1">
                <svg class="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>
                </svg>
                继续追问
                <span
                  class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full text-[9px] font-bold text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 cursor-help border border-gray-300/70 dark:border-gray-600/70"
                  :title="FOLLOWUPS_SECTION_TIP"
                  aria-label="继续追问说明"
                  @click.stop
                >?</span>
              </span>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-[10px] font-bold text-gray-500 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400 transition-all px-2 py-0.5 rounded-md border border-gray-200/80 dark:border-gray-700 disabled:opacity-50"
                :disabled="isFollowupsRefreshDisabled(group)"
                @click.stop="handleRefreshGroupFollowups(group)"
              >
                <svg
                  class="w-3 h-3"
                  :class="{ 'animate-spin': refreshingFollowupGroupIds[group.id || group.title] }"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                </svg>
                <span>换一批</span>
              </button>
            </div>
            <div v-if="refreshingFollowupGroupIds[group.id || group.title]" class="flex flex-wrap gap-2 animate-pulse-slow">
              <div
                v-for="i in 2"
                :key="`followup-sk-${i}`"
                class="inline-flex h-7 w-28 rounded-lg border border-gray-200/60 dark:border-gray-700/60 bg-gray-50/60 dark:bg-gray-800/30"
              />
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <div
                v-for="followup in group.followups"
                :key="followup.query"
                class="group/follow inline-flex items-stretch rounded-lg border border-gray-200/50 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/20 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:border-blue-100 dark:hover:border-blue-900/40 transition-all shadow-xs overflow-hidden"
              >
                <button
                  type="button"
                  class="px-2.5 py-1 text-left bg-transparent hover:bg-blue-50/30 dark:hover:bg-blue-900/20 transition-colors cursor-pointer font-medium"
                  @click="handleFollowupClick(followup, group, 'send')"
                >
                  <span>{{ followup.label }}</span>
                </button>
                <button
                  type="button"
                  class="opacity-0 group-hover/follow:opacity-100 flex items-center justify-center px-1.5 border-l border-gray-200/50 dark:border-gray-700 hover:bg-blue-50/30 dark:hover:bg-blue-900/20 transition-all duration-200 cursor-pointer"
                  title="填入输入框微调"
                  aria-label="填入输入框微调"
                  @click.stop="handleFollowupClick(followup, group, 'fill')"
                >
                  <svg class="w-3 h-3 text-gray-400 group-hover/follow:text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.83 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </article>
      </div>

      <div
        v-if="hiddenPortalCardCount > 0"
        class="mt-3 flex justify-center"
      >
        <button
          type="button"
          class="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl border border-dashed border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 text-xs font-bold text-gray-600 dark:text-gray-300 hover:bg-gray-100/80 dark:hover:bg-gray-800/50 transition-colors"
          @click="portalCardsExpanded = true"
        >
          <span>还有 {{ hiddenPortalCardCount }} 个数据集未展示</span>
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>
      <div
        v-else-if="portalCardsExpanded && displayGroups.length > PORTAL_DEFAULT_VISIBLE_CARDS && !isFilteringPortal"
        class="mt-2 flex justify-center"
      >
        <button
          type="button"
          class="text-[11px] font-semibold text-gray-500 dark:text-gray-400 hover:text-primary transition-colors"
          @click="portalCardsExpanded = false"
        >
          收起部分数据集
        </button>
      </div>

      <!-- Empty State: no permission -->
      <div
        v-if="isNoPermissionEmpty"
        class="bg-amber-50/30 dark:bg-amber-950/10 border border-dashed border-amber-200 dark:border-amber-900/40 rounded-xl p-8 flex flex-col items-center justify-center text-center space-y-2 select-none"
      >
        <div class="text-amber-400 dark:text-amber-600">
          <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        </div>
        <h4 class="text-xs font-bold text-amber-800 dark:text-amber-200">暂无可用数据集</h4>
        <p class="text-[10px] text-amber-700/80 dark:text-amber-300/80 max-w-[260px] leading-relaxed">
          当前账号尚未开通 ChatBI 数据查询权限。请联系系统管理员为您配置数据集访问权限后再试。
        </p>
      </div>

      <!-- Empty State: search / filter -->
      <div
        v-else-if="filteredGroups.length === 0 && !initialLoading"
        class="bg-gray-50/20 dark:bg-gray-900/10 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl p-8 flex flex-col items-center justify-center text-center space-y-2 select-none"
      >
        <div class="text-gray-300 dark:text-gray-600">
          <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <h4 class="text-xs font-bold text-gray-500 dark:text-gray-400">未找到相关数据场景</h4>
        <p class="text-[10px] text-gray-400 dark:text-gray-500 max-w-[240px]">尝试精简搜索词，或切换其他分类标签</p>
        <div v-if="emptySearchSuggestions.length" class="flex flex-wrap gap-1.5 justify-center pt-1">
          <button
            v-for="tag in emptySearchSuggestions"
            :key="tag"
            type="button"
            class="px-2 py-0.5 text-[10px] rounded-full border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-primary/40 hover:text-primary transition-colors"
            @click="applyEmptySearchSuggestion(tag)"
          >
            试试：{{ tag }}
          </button>
        </div>
      </div>

    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted, onUnmounted } from "vue";
import axios from "@/utils/axios";
import { useToast } from "@/composables/useToast";

const { showToast } = useToast();

const QUESTIONS_SECTION_TIP =
  "该场景的入门示例问题：点击即可直接发起查询，适合快速了解核心指标、趋势与排名。";
const FOLLOWUPS_SECTION_TIP =
  "延伸探索型追问：适合在已有结果基础上深挖关联维度、口径说明或下一步分析方向。";
const NO_MORE_UNIQUE_QUESTIONS_TIP = "暂无更多不同问题，稍后再试";

interface DatasetCapabilityQuestion {
  label: string;
  query: string;
  type?: string;
  click_count?: number;
  last_clicked_at?: string;
}

interface DatasetColumnInfo {
  name: string;
  term: string;
  type: string;
  description: string;
}

interface DatasetCapabilityRelatedData {
  dataset?: string;
  display_name?: string;
  tables?: string[];
  table_descriptions?: Array<{ name: string; description?: string }>;
  table_columns?: Record<string, DatasetColumnInfo[]>;
  table_physical_names?: Record<string, string>;
}

interface DatasetCapabilityGroup {
  id?: string;
  title: string;
  summary: string;
  tags?: string[];
  metrics?: string[];
  questions?: DatasetCapabilityQuestion[];
  related_data?: DatasetCapabilityRelatedData[];
  followups?: DatasetCapabilityQuestion[];
}

interface DatasetNavigationPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: DatasetCapabilityGroup[];
  markdown?: string;
  is_fallback?: boolean;
  from_cache?: boolean;
  has_datasets?: boolean;
  llm_generation_failed?: boolean;
  llm_error_message?: string | null;
}

const props = withDefaults(defineProps<{
  payload: DatasetNavigationPayload;
  initialLoading?: boolean;
  backgroundRefreshing?: boolean;
}>(), {
  initialLoading: false,
  backgroundRefreshing: false,
});

const formatGroupSummary = (text: string): string => {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
};

const formatTagLabel = (tag: string, maxLen = 18): string => {
  const cleaned = String(tag || "").trim();
  if (!cleaned) return "";
  if (cleaned.length <= maxLen) return cleaned;
  return `${cleaned.slice(0, maxLen)}…`;
};

const emit = defineEmits<{
  (event: "quick-question", query: string, action?: "send" | "fill"): void;
  (event: "record-question-click", payload: { query: string; label?: string; group_id?: string }): void;
  (event: "clear-question-click", payload: { query: string }): void;
  (event: "refresh"): void;
  (event: "execute-saved-report", payload: { id: string; title: string; sql_content: string }): void;
}>();

const menuContainer = ref<HTMLElement | null>(null);
const isRefreshing = ref(false);
let refreshSafetyTimer: ReturnType<typeof setTimeout> | null = null;

const showRefreshBusy = computed(() => isRefreshing.value || props.backgroundRefreshing);
const refreshDisabled = computed(
  () =>
    props.initialLoading
    || showRefreshBusy.value
    || props.payload?.has_datasets === false,
);

const clearRefreshBusy = () => {
  isRefreshing.value = false;
  if (refreshSafetyTimer) {
    clearTimeout(refreshSafetyTimer);
    refreshSafetyTimer = null;
  }
};
const expandedGroups = ref<Record<string, boolean>>({});
const refreshingGroupIds = ref<Record<string, boolean>>({});
const refreshingFollowupGroupIds = ref<Record<string, boolean>>({});
const GROUP_REFRESH_COOLDOWN_MS = 4000;
type GroupRefreshScope = "questions" | "followups";
const groupRefreshCooldownUntil = ref<Record<string, number>>({});
const groupRefreshCooldownTimers: Record<string, ReturnType<typeof setTimeout>> = {};
const groupRefreshCooldownTick = ref(0);
const pinnedTableDictionary = ref<string | null>(null);
const popoverStyles = ref<Record<string, Record<string, string>>>({});
const tableRecommendState = ref<Record<string, {
  loading: boolean;
  questions: Array<{ label: string; query: string }>;
  error?: string;
}>>({});

// ===== 门户个人偏好（置顶 + 拖拽排序 + 展开状态 + 常问备份）=====
const pinnedGroupIds = ref<string[]>([]);
const cardOrder = ref<string[]>([]);          // 拖拽自定义排序
const localQuestionClicks = ref<Record<string, number>>({}); // 常问本地点击备份
const isSavingPrefs = ref(false);
let prefsDebounceSaveTimer: ReturnType<typeof setTimeout> | null = null;

const isPinned = (group: DatasetCapabilityGroup): boolean => {
  const id = group.id || group.title;
  return pinnedGroupIds.value.includes(id);
};

const loadPortalPrefs = async () => {
  try {
    const res = await axios.get("/api/portal/portal-prefs");
    const data = res.data?.data;
    if (!data) return;
    if (Array.isArray(data.pinned_group_ids)) pinnedGroupIds.value = data.pinned_group_ids;
    if (Array.isArray(data.card_order))        cardOrder.value = data.card_order;
    if (Array.isArray(data.expanded_group_ids)) {
      const expanded: Record<string, boolean> = {};
      for (const id of data.expanded_group_ids) expanded[id] = true;
      expandedGroups.value = expanded;
    }
    if (data.question_clicks && typeof data.question_clicks === 'object') {
      localQuestionClicks.value = data.question_clicks;
    }
  } catch (error) {
    console.error("Failed to load portal prefs:", error);
  }
};

const savePortalPrefs = async (immediate = false) => {
  const doSave = async () => {
    if (isSavingPrefs.value) return;
    isSavingPrefs.value = true;
    try {
      await axios.put("/api/portal/portal-prefs", {
        pinned_group_ids: pinnedGroupIds.value,
        card_order: cardOrder.value,
        expanded_group_ids: Object.entries(expandedGroups.value)
          .filter(([, v]) => v).map(([k]) => k),
        question_clicks: localQuestionClicks.value,
      });
    } catch (error) {
      console.error("Failed to save portal prefs:", error);
    } finally {
      isSavingPrefs.value = false;
    }
  };

  if (immediate) {
    if (prefsDebounceSaveTimer) { clearTimeout(prefsDebounceSaveTimer); prefsDebounceSaveTimer = null; }
    await doSave();
  } else {
    if (prefsDebounceSaveTimer) clearTimeout(prefsDebounceSaveTimer);
    prefsDebounceSaveTimer = setTimeout(doSave, 1200);
  }
};

const togglePinGroup = async (event: MouseEvent, group: DatasetCapabilityGroup) => {
  event.stopPropagation();
  const id = group.id || group.title;
  if (!id) return;
  const idx = pinnedGroupIds.value.indexOf(id);
  if (idx === -1) {
    pinnedGroupIds.value = [id, ...pinnedGroupIds.value];
    // 置顶时同时把该卡片移到 cardOrder 最前
    const orderWithout = cardOrder.value.filter((v) => v !== id);
    cardOrder.value = [id, ...orderWithout];
    showToast(`已置顶「${group.title}」`, "success");
  } else {
    pinnedGroupIds.value = pinnedGroupIds.value.filter((v) => v !== id);
    showToast(`已取消置顶「${group.title}」`, "success");
  }
  await savePortalPrefs(true);
};

// ===== 拖拽排序 =====
const dragSourceId = ref<string | null>(null);
const dragOverId = ref<string | null>(null);
const canDragCards = computed(() => !isFilteringPortal.value && !showRefreshBusy.value);

const handleDragStart = (event: DragEvent, groupId: string) => {
  dragSourceId.value = groupId;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', groupId);
  }
};

const handleDragOver = (event: DragEvent, groupId: string) => {
  if (!canDragCards.value) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
  if (groupId !== dragSourceId.value) dragOverId.value = groupId;
};

const handleDragLeave = (event: DragEvent) => {
  // 只在真正离开卡片区域时清除（避免子元素触发）
  const related = event.relatedTarget as HTMLElement | null;
  const card = (event.currentTarget as HTMLElement);
  if (!related || !card.contains(related)) dragOverId.value = null;
};

const handleDrop = (event: DragEvent, targetId: string) => {
  event.preventDefault();
  dragOverId.value = null;
  const sourceId = dragSourceId.value;
  if (!sourceId || sourceId === targetId) return;

  // 以当前渲染顺序为基准构建新排序
  const currentOrder = displayGroups.value.map(({ group }) => group.id || group.title);
  const sourceIdx = currentOrder.indexOf(sourceId);
  const targetIdx = currentOrder.indexOf(targetId);
  if (sourceIdx === -1 || targetIdx === -1) return;

  const newOrder = [...currentOrder];
  newOrder.splice(sourceIdx, 1);
  newOrder.splice(targetIdx, 0, sourceId);
  cardOrder.value = newOrder;
  dragSourceId.value = null;
  savePortalPrefs(true);
};

const handleDragEnd = () => {
  dragSourceId.value = null;
  dragOverId.value = null;
};
// ===========================

const buildTableDictionaryKey = (
  group: DatasetCapabilityGroup,
  related: DatasetCapabilityRelatedData,
  table: string,
) => `${group.id || group.title}_${related.dataset || related.display_name || ""}_${table}`;

const handleGlobalClick = () => {
  pinnedTableDictionary.value = null;
};

const updatePopoverStyle = (badgeEl: HTMLElement, uniqueId: string) => {
  if (!badgeEl) {
    console.error("updatePopoverStyle fail: badgeEl is null");
    return;
  }
  if (!menuContainer.value) {
    console.error("updatePopoverStyle fail: menuContainer.value is null");
    return;
  }
  
  const badgeRect = badgeEl.getBoundingClientRect();
  const containerRect = menuContainer.value.getBoundingClientRect();
  
  const popoverWidth = 288; // w-72 is 288px
  const badgeCenterRelative = (badgeRect.left + badgeRect.width / 2) - containerRect.left;
  
  let left = badgeCenterRelative - popoverWidth / 2;
  const minLeft = 8;
  if (left < minLeft) {
    left = minLeft;
  }
  
  const maxLeft = containerRect.width - popoverWidth - 8;
  if (left > maxLeft) {
    left = maxLeft;
  }
  
  const badgeLeftRelative = badgeRect.left - containerRect.left;
  const popoverLeftOffset = left - badgeLeftRelative;
  
  console.error("updatePopoverStyle SUCCESS:", {
    uniqueId,
    badgeRectLeft: badgeRect.left,
    badgeWidth: badgeRect.width,
    containerRectLeft: containerRect.left,
    containerRectWidth: containerRect.width,
    badgeCenterRelative,
    left,
    badgeLeftRelative,
    popoverLeftOffset
  });
  
  popoverStyles.value[uniqueId] = {
    left: `${popoverLeftOffset}px`,
    transform: 'none',
  };
};

const handleTableDictionaryClick = (
  event: MouseEvent,
  group: DatasetCapabilityGroup,
  related: DatasetCapabilityRelatedData,
  table: string,
) => {
  const uniqueId = buildTableDictionaryKey(group, related, table);
  if (pinnedTableDictionary.value === uniqueId) {
    pinnedTableDictionary.value = null;
    return;
  }
  pinnedTableDictionary.value = uniqueId;
  const target = event.currentTarget as HTMLElement;
  if (target) {
    updatePopoverStyle(target, uniqueId);
  }
};

// 黄金报表业务逻辑
const savedReports = ref<any[]>([]);
const loadingReports = ref(false);
const showSavedReportsCollapse = ref(true); // 默认收起
const showFrequentCollapse = ref(false); // 我常问默认展开

const fetchSavedReports = async () => {
  loadingReports.value = true;
  try {
    const res = await axios.get("/api/portal/saved-reports");
    if (res.data && res.data.data) {
      savedReports.value = res.data.data;
    }
  } catch (error) {
    console.error("Failed to fetch saved reports:", error);
  } finally {
    loadingReports.value = false;
  }
};

const handleDeleteReport = async (report: any) => {
  try {
    await axios.delete(`/api/portal/saved-reports/${report.id}`);
    showToast("删除暂存报表成功", "success");
    await fetchSavedReports();
  } catch (error: any) {
    console.error("Failed to delete saved report:", error);
    const detail = error.response?.data?.detail || "删除失败";
    showToast(typeof detail === 'object' ? JSON.stringify(detail) : detail, "error");
  }
};

const handleExecuteSavedReportClick = (report: any) => {
  emit("execute-saved-report", {
    id: report.id,
    title: report.title,
    sql_content: report.sql_content,
  });
};

const formatDate = (isoString: string) => {
  if (!isoString) return "";
  try {
    const date = new Date(isoString);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  } catch (e) {
    return isoString;
  }
};

watch(() => props.payload, () => {
  fetchSavedReports();
}, { deep: true });

onMounted(() => {
  document.addEventListener("click", handleGlobalClick);
  fetchSavedReports();
  loadPortalPrefs();
});

onUnmounted(() => {
  document.removeEventListener("click", handleGlobalClick);
  if (upgradedBannerTimer) {
    clearTimeout(upgradedBannerTimer);
    upgradedBannerTimer = null;
  }
  for (const timer of Object.values(groupRefreshCooldownTimers)) {
    clearTimeout(timer);
  }
});

const searchQuery = ref("");
const selectedTag = ref("All");
const showSearchBar = ref(false);

watch(showSearchBar, (val) => {
  if (!val) {
    searchQuery.value = "";
    selectedTag.value = "All";
  }
});
const PORTAL_DEFAULT_VISIBLE_CARDS = 5;
const portalCardsExpanded = ref(false);
const upgradedFromFallback = ref(false);
let upgradedBannerTimer: ReturnType<typeof setTimeout> | null = null;

const groupPopularityScore = (group: DatasetCapabilityGroup): number => {
  const questions = group.questions || [];
  return questions.reduce((max, q) => Math.max(max, q.click_count || 0), 0);
};

const sortedQuestions = (questions?: DatasetCapabilityQuestion[]) => {
  return [...(questions || [])].sort((a, b) => (b.click_count || 0) - (a.click_count || 0));
};

const countRelatedTables = (group: DatasetCapabilityGroup): number => {
  return (group.related_data || []).reduce((sum, item) => sum + (item.tables?.length || 0), 0);
};

const portalStatus = computed(() => {
  if (props.initialLoading) return "loading";
  if (props.backgroundRefreshing) return "refreshing";
  if (props.payload?.has_datasets === false) return "no_permission";
  if (props.payload?.llm_generation_failed && props.payload?.is_fallback) return "llm_failed";
  if (props.payload?.is_fallback) return "fallback";
  return "ready";
});

const showStatusBanner = computed(
  () =>
    props.payload?.has_datasets !== false
    && (portalStatus.value !== "ready" || showReadyBanner.value),
);

const formattedGeneratedAt = computed(() => {
  if (!props.payload.generated_at) return "";
  const date = new Date(props.payload.generated_at);
  if (Number.isNaN(date.getTime())) return props.payload.generated_at;
  return date.toLocaleString();
});

const cacheAgeLabel = computed(() => {
  if (!props.payload.generated_at) return "";
  const date = new Date(props.payload.generated_at);
  if (Number.isNaN(date.getTime())) return "";
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return "刚刚生成";
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚生成";
  if (diffMin < 60) return `${diffMin} 分钟前生成`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} 小时前生成`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return `${diffD} 天前生成`;
  return formattedGeneratedAt.value;
});

const cacheHashShort = computed(() => {
  const hash = props.payload.dataset_menu_hash;
  return hash ? hash.slice(0, 8) : "";
});

const cacheSourceLabel = computed(() => {
  if (props.payload.from_cache === true) return "缓存命中";
  if (props.payload.from_cache === false) return "本次新生成";
  return "";
});

const isNoPermissionEmpty = computed(() => {
  if (props.initialLoading || props.backgroundRefreshing) return false;
  if (props.payload.has_datasets === false) return true;
  const groupCount = (props.payload.groups || []).length;
  const datasetCount = props.payload.dataset_count ?? groupCount;
  return datasetCount === 0 && groupCount === 0 && !props.payload.is_fallback;
});

const showReadyBanner = computed(
  () => upgradedFromFallback.value && portalStatus.value === "ready",
);

const isFilteringPortal = computed(
  () => !!searchQuery.value.trim() || selectedTag.value !== "All",
);

const statusBannerClass = computed(() => {
  if (showReadyBanner.value) {
    return "border-emerald-100 bg-emerald-50/70 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200";
  }
  switch (portalStatus.value) {
    case "loading":
      return "border-blue-100 bg-blue-50/70 text-blue-700 dark:border-blue-900/40 dark:bg-blue-950/30 dark:text-blue-200";
    case "refreshing":
      return "border-blue-100 bg-blue-50/50 text-blue-600 dark:border-blue-900/40 dark:bg-blue-950/20 dark:text-blue-300";
    case "llm_failed":
      return "border-red-100 bg-red-50/70 text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-200";
    case "fallback":
      return "border-amber-100 bg-amber-50/70 text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200";
    default:
      return "border-emerald-100 bg-emerald-50/60 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-300";
  }
});

const statusBannerIcon = computed(() => {
  if (portalStatus.value === "fallback") return "⚠️";
  if (portalStatus.value === "llm_failed") return "⚠️";
  if (portalStatus.value === "ready") return "✓";
  return "…";
});

const statusBannerText = computed(() => {
  if (showReadyBanner.value) {
    return "完整 AI 场景卡片已生成，推荐问题与摘要已更新，可直接点击提问。";
  }
  switch (portalStatus.value) {
    case "loading":
      return "正在生成数据门户，首次加载约需 15–30 秒，请稍候…";
    case "refreshing":
      return "正在后台刷新完整门户内容…";
    case "llm_failed": {
      const detail = String(props.payload.llm_error_message || "").trim();
      const suffix = detail ? `（${detail}）` : "";
      return `AI 模型暂不可用，已展示基础场景目录${suffix}。请检查模型配置与网络，或点击右上角刷新重试。`;
    }
    case "fallback":
      return "正在生成完整 AI 场景卡片，可先点击问题开始查数。";
    default: {
      const parts = ["门户已就绪"];
      if (cacheAgeLabel.value) parts.push(cacheAgeLabel.value);
      if (cacheSourceLabel.value) parts.push(cacheSourceLabel.value);
      if (cacheHashShort.value) parts.push(`版本 #${cacheHashShort.value}`);
      return parts.join(" · ");
    }
  }
});

const refreshButtonTitle = computed(() => {
  const parts = ["刷新数据门户"];
  if (cacheHashShort.value) parts.push(`版本 #${cacheHashShort.value}`);
  if (cacheAgeLabel.value) parts.push(cacheAgeLabel.value);
  if (cacheSourceLabel.value) parts.push(cacheSourceLabel.value);
  return parts.join(" · ");
});

const frequentQuestions = computed(() => {
  const merged = new Map<string, { question: DatasetCapabilityQuestion; group: DatasetCapabilityGroup }>();
  for (const group of props.payload.groups || []) {
    for (const question of group.questions || []) {
      const query = String(question.query || "").trim();
      // 合并服务端数 + 本地备份数，取最大值
      const localCount = localQuestionClicks.value[query] || 0;
      const serverCount = question.click_count || 0;
      const clicks = Math.max(localCount, serverCount);
      if (!query || clicks <= 0) continue;
      const existing = merged.get(query);
      if (!existing || clicks > Math.max(existing.question.click_count || 0, localQuestionClicks.value[query] || 0)) {
        merged.set(query, { question: { ...question, click_count: clicks }, group });
      }
    }
  }
  return Array.from(merged.values())
    .sort((a, b) => (b.question.click_count || 0) - (a.question.click_count || 0))
    .slice(0, 5);
});

const showFrequentSection = computed(() => {
  return !searchQuery.value.trim() && selectedTag.value === "All" && frequentQuestions.value.length > 0;
});

const emptySearchSuggestions = computed(() => allTags.value.slice(0, 3));

const tagFilterClass = (tag: string) => {
  if (selectedTag.value !== tag) {
    return "bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800";
  }
  const owner = (props.payload.groups || []).find((group) => (group.tags || []).includes(tag));
  const theme = owner ? getGroupVisuals(owner, 0) : getGroupVisuals({ id: tag, title: tag }, 0);
  return `${theme.tag} ring-1 ring-current/20 shadow-xs`;
};

const applyEmptySearchSuggestion = (tag: string) => {
  selectedTag.value = tag;
  searchQuery.value = "";
};

const handleFrequentQuestionClick = (
  item: { question: DatasetCapabilityQuestion; group: DatasetCapabilityGroup },
  action: "send" | "fill" = "send"
) => {
  handleQuestionClick(item.question, item.group, action);
};

const handleClearFrequentQuestion = (item: { question: DatasetCapabilityQuestion; group: DatasetCapabilityGroup }) => {
  const query = String(item.question.query || "").trim();
  if (!query) return;
  // 同时清除本地备份数
  if (localQuestionClicks.value[query]) {
    delete localQuestionClicks.value[query];
    savePortalPrefs();
  }
  emit("clear-question-click", { query });
};

// 动态去重提取出当前有权访问的所有卡片 tags
const allTags = computed(() => {
  const tagsSet = new Set<string>();
  const groups = props.payload.groups || [];
  for (const group of groups) {
    if (group.tags) {
      for (const tag of group.tags) {
        const cleaned = String(tag || "").trim();
        if (cleaned) {
          tagsSet.add(cleaned);
        }
      }
    }
  }
  return Array.from(tagsSet);
});

// 图标与视觉主题定义
interface GroupVisualTheme {
  key: string;
  card: string;
  cardBorder: string;
  cardHover: string;
  decor: string;
  iconBorder: string;
  icon: string;
  title: string;
  tag: string;
  quote: string;
  strongColor: string;
  sectionLabel: string;
  refreshBtn: string;
  questionBtn: string;
  questionSkeleton: string;
  divider: string;
}

const CARD_THEMES: GroupVisualTheme[] = [
  {
    key: "blue",
    card: "bg-gradient-to-br from-blue-50/95 via-white to-sky-50/50 dark:from-blue-950/35 dark:via-gray-900/50 dark:to-sky-950/15",
    cardBorder: "border-blue-100/90 dark:border-blue-900/45",
    cardHover: "hover:border-blue-200 dark:hover:border-blue-700/60",
    decor: "bg-blue-400",
    iconBorder: "border-blue-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>`,
    title: "text-blue-950 dark:text-blue-100",
    tag: "bg-blue-100/90 text-blue-700 border-blue-200/70 dark:bg-blue-900/50 dark:text-blue-200 dark:border-blue-800/60",
    quote: "border-l-blue-500 bg-blue-50/70 text-blue-900/75 dark:bg-blue-950/35 dark:text-blue-200/90 dark:border-l-blue-400",
    strongColor: "#1d4ed8",
    sectionLabel: "text-blue-500/80 dark:text-blue-400/80",
    refreshBtn: "text-blue-700 bg-white/70 border-blue-200/70 hover:bg-blue-50 dark:text-blue-300 dark:bg-blue-950/40 dark:border-blue-800/50 dark:hover:bg-blue-900/50",
    questionBtn: "border-blue-200/80 bg-white/80 text-blue-800 hover:bg-blue-50 hover:border-blue-300 dark:border-blue-800/50 dark:bg-blue-950/25 dark:text-blue-200 dark:hover:bg-blue-900/40",
    questionSkeleton: "bg-blue-50/40 border-blue-100/30 dark:bg-blue-950/15 dark:border-blue-900/20",
    divider: "border-blue-100/80 dark:border-blue-900/35",
  },
  {
    key: "violet",
    card: "bg-gradient-to-br from-violet-50/95 via-white to-purple-50/40 dark:from-violet-950/35 dark:via-gray-900/50 dark:to-purple-950/15",
    cardBorder: "border-violet-100/90 dark:border-violet-900/45",
    cardHover: "hover:border-violet-200 dark:hover:border-violet-700/60",
    decor: "bg-violet-400",
    iconBorder: "border-violet-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>`,
    title: "text-violet-950 dark:text-violet-100",
    tag: "bg-violet-100/90 text-violet-700 border-violet-200/70 dark:bg-violet-900/50 dark:text-violet-200 dark:border-violet-800/60",
    quote: "border-l-violet-500 bg-violet-50/70 text-violet-900/75 dark:bg-violet-950/35 dark:text-violet-200/90 dark:border-l-violet-400",
    strongColor: "#6d28d9",
    sectionLabel: "text-violet-500/80 dark:text-violet-400/80",
    refreshBtn: "text-violet-700 bg-white/70 border-violet-200/70 hover:bg-violet-50 dark:text-violet-300 dark:bg-violet-950/40 dark:border-violet-800/50 dark:hover:bg-violet-900/50",
    questionBtn: "border-violet-200/80 bg-white/80 text-violet-800 hover:bg-violet-50 hover:border-violet-300 dark:border-violet-800/50 dark:bg-violet-950/25 dark:text-violet-200 dark:hover:bg-violet-900/40",
    questionSkeleton: "bg-violet-50/40 border-violet-100/30 dark:bg-violet-950/15 dark:border-violet-900/20",
    divider: "border-violet-100/80 dark:border-violet-900/35",
  },
  {
    key: "emerald",
    card: "bg-gradient-to-br from-emerald-50/95 via-white to-teal-50/40 dark:from-emerald-950/35 dark:via-gray-900/50 dark:to-teal-950/15",
    cardBorder: "border-emerald-100/90 dark:border-emerald-900/45",
    cardHover: "hover:border-emerald-200 dark:hover:border-emerald-700/60",
    decor: "bg-emerald-400",
    iconBorder: "border-emerald-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>`,
    title: "text-emerald-950 dark:text-emerald-100",
    tag: "bg-emerald-100/90 text-emerald-700 border-emerald-200/70 dark:bg-emerald-900/50 dark:text-emerald-200 dark:border-emerald-800/60",
    quote: "border-l-emerald-500 bg-emerald-50/70 text-emerald-900/75 dark:bg-emerald-950/35 dark:text-emerald-200/90 dark:border-l-emerald-400",
    strongColor: "#047857",
    sectionLabel: "text-emerald-500/80 dark:text-emerald-400/80",
    refreshBtn: "text-emerald-700 bg-white/70 border-emerald-200/70 hover:bg-emerald-50 dark:text-emerald-300 dark:bg-emerald-950/40 dark:border-emerald-800/50 dark:hover:bg-emerald-900/50",
    questionBtn: "border-emerald-200/80 bg-white/80 text-emerald-800 hover:bg-emerald-50 hover:border-emerald-300 dark:border-emerald-800/50 dark:bg-emerald-950/25 dark:text-emerald-200 dark:hover:bg-emerald-900/40",
    questionSkeleton: "bg-emerald-50/40 border-emerald-100/30 dark:bg-emerald-950/15 dark:border-emerald-900/20",
    divider: "border-emerald-100/80 dark:border-emerald-900/35",
  },
  {
    key: "amber",
    card: "bg-gradient-to-br from-amber-50/95 via-white to-orange-50/35 dark:from-amber-950/30 dark:via-gray-900/50 dark:to-orange-950/15",
    cardBorder: "border-amber-100/90 dark:border-amber-900/45",
    cardHover: "hover:border-amber-200 dark:hover:border-amber-700/60",
    decor: "bg-amber-400",
    iconBorder: "border-amber-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>`,
    title: "text-amber-950 dark:text-amber-100",
    tag: "bg-amber-100/90 text-amber-800 border-amber-200/70 dark:bg-amber-900/50 dark:text-amber-200 dark:border-amber-800/60",
    quote: "border-l-amber-500 bg-amber-50/70 text-amber-900/75 dark:bg-amber-950/35 dark:text-amber-200/90 dark:border-l-amber-400",
    strongColor: "#b45309",
    sectionLabel: "text-amber-600/80 dark:text-amber-400/80",
    refreshBtn: "text-amber-800 bg-white/70 border-amber-200/70 hover:bg-amber-50 dark:text-amber-300 dark:bg-amber-950/40 dark:border-amber-800/50 dark:hover:bg-amber-900/50",
    questionBtn: "border-amber-200/80 bg-white/80 text-amber-900 hover:bg-amber-50 hover:border-amber-300 dark:border-amber-800/50 dark:bg-amber-950/25 dark:text-amber-200 dark:hover:bg-amber-900/40",
    questionSkeleton: "bg-amber-50/40 border-amber-100/30 dark:bg-amber-950/15 dark:border-amber-900/20",
    divider: "border-amber-100/80 dark:border-amber-900/35",
  },
  {
    key: "rose",
    card: "bg-gradient-to-br from-rose-50/95 via-white to-pink-50/35 dark:from-rose-950/30 dark:via-gray-900/50 dark:to-pink-950/15",
    cardBorder: "border-rose-100/90 dark:border-rose-900/45",
    cardHover: "hover:border-rose-200 dark:hover:border-rose-700/60",
    decor: "bg-rose-400",
    iconBorder: "border-rose-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>`,
    title: "text-rose-950 dark:text-rose-100",
    tag: "bg-rose-100/90 text-rose-700 border-rose-200/70 dark:bg-rose-900/50 dark:text-rose-200 dark:border-rose-800/60",
    quote: "border-l-rose-500 bg-rose-50/70 text-rose-900/75 dark:bg-rose-950/35 dark:text-rose-200/90 dark:border-l-rose-400",
    strongColor: "#be123c",
    sectionLabel: "text-rose-500/80 dark:text-rose-400/80",
    refreshBtn: "text-rose-700 bg-white/70 border-rose-200/70 hover:bg-rose-50 dark:text-rose-300 dark:bg-rose-950/40 dark:border-rose-800/50 dark:hover:bg-rose-900/50",
    questionBtn: "border-rose-200/80 bg-white/80 text-rose-800 hover:bg-rose-50 hover:border-rose-300 dark:border-rose-800/50 dark:bg-rose-950/25 dark:text-rose-200 dark:hover:bg-rose-900/40",
    questionSkeleton: "bg-rose-50/40 border-rose-100/30 dark:bg-rose-950/15 dark:border-rose-900/20",
    divider: "border-rose-100/80 dark:border-rose-900/35",
  },
  {
    key: "cyan",
    card: "bg-gradient-to-br from-cyan-50/95 via-white to-sky-50/35 dark:from-cyan-950/30 dark:via-gray-900/50 dark:to-sky-950/15",
    cardBorder: "border-cyan-100/90 dark:border-cyan-900/45",
    cardHover: "hover:border-cyan-200 dark:hover:border-cyan-700/60",
    decor: "bg-cyan-400",
    iconBorder: "border-cyan-400/20",
    icon: `<svg fill="none" stroke="#ffffff" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>`,
    title: "text-cyan-950 dark:text-cyan-100",
    tag: "bg-cyan-100/90 text-cyan-800 border-cyan-200/70 dark:bg-cyan-900/50 dark:text-cyan-200 dark:border-cyan-800/60",
    quote: "border-l-cyan-500 bg-cyan-50/70 text-cyan-900/75 dark:bg-cyan-950/35 dark:text-cyan-200/90 dark:border-l-cyan-400",
    strongColor: "#0e7490",
    sectionLabel: "text-cyan-600/80 dark:text-cyan-400/80",
    refreshBtn: "text-cyan-800 bg-white/70 border-cyan-200/70 hover:bg-cyan-50 dark:text-cyan-300 dark:bg-cyan-950/40 dark:border-cyan-800/50 dark:hover:bg-cyan-900/50",
    questionBtn: "border-cyan-200/80 bg-white/80 text-cyan-900 hover:bg-cyan-50 hover:border-cyan-300 dark:border-cyan-800/50 dark:bg-cyan-950/25 dark:text-cyan-200 dark:hover:bg-cyan-900/40",
    questionSkeleton: "bg-cyan-50/40 border-cyan-100/30 dark:bg-cyan-950/15 dark:border-cyan-900/20",
    divider: "border-cyan-100/80 dark:border-cyan-900/35",
  },
];

const hashThemeIndex = (seed: string): number => {
  const normalized = seed.trim();
  if (!normalized) return 0;
  let hash = 0;
  for (let i = 0; i < normalized.length; i += 1) {
    hash = (hash * 31 + normalized.charCodeAt(i)) >>> 0;
  }
  return hash % CARD_THEMES.length;
};

const getGroupVisuals = (
  group: Pick<DatasetCapabilityGroup, "id" | "title">,
  fallbackIndex = 0,
): GroupVisualTheme => {
  const seed = String(group.id || group.title || fallbackIndex);
  return CARD_THEMES[hashThemeIndex(seed)] || CARD_THEMES[0]!;
};

// 计算属性：联合搜索与过滤
const filteredGroups = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  const tag = selectedTag.value;
  const groups = props.payload.groups || [];

  return groups.filter((group) => {
    // 1. Tag 过滤
    if (tag !== "All") {
      const groupTags = group.tags || [];
      if (!groupTags.some((t) => String(t).trim() === tag)) {
        return false;
      }
    }

    // 2. 搜索框过滤
    if (query) {
      const title = (group.title || "").toLowerCase();
      const summary = (group.summary || "").toLowerCase();
      const matchTitleOrSummary = title.includes(query) || summary.includes(query);
      if (matchTitleOrSummary) return true;

      // 匹配关联物理表
      const relatedData = group.related_data || [];
      const matchTable = relatedData.some((related) => {
        const dataset = (related.dataset || "").toLowerCase();
        const displayName = (related.display_name || "").toLowerCase();
        const tables = (related.tables || []).some((t) => t.toLowerCase().includes(query));
        return dataset.includes(query) || displayName.includes(query) || tables;
      });
      if (matchTable) return true;

      // 匹配推荐问题
      const questions = group.questions || [];
      const matchQuestion = questions.some((q) => {
        const label = (q.label || "").toLowerCase();
        const queryText = (q.query || "").toLowerCase();
        return label.includes(query) || queryText.includes(query);
      });
      if (matchQuestion) return true;

      return false;
    }

    return true;
  });
});

const displayGroups = computed(() => {
  const pinned = pinnedGroupIds.value;
  const order = cardOrder.value;

  const sorted = [...filteredGroups.value].sort((a, b) => {
    const aId = a.id || a.title;
    const bId = b.id || b.title;

    if (order.length > 0) {
      // 有明确拖拽排序时，以 cardOrder 为主
      const aIdx = order.indexOf(aId);
      const bIdx = order.indexOf(bId);
      if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx;
      if (aIdx !== -1) return -1; // 在排序列表中的排前
      if (bIdx !== -1) return 1;
      // 不在排序列表中的，按热度退化排到后面
      return groupPopularityScore(b) - groupPopularityScore(a);
    }

    // 无自定义排序时：置顶优先，然后按热度
    const aPinned = pinned.includes(aId) ? 1 : 0;
    const bPinned = pinned.includes(bId) ? 1 : 0;
    if (bPinned !== aPinned) return bPinned - aPinned;
    if (aPinned && bPinned) return pinned.indexOf(aId) - pinned.indexOf(bId);
    return groupPopularityScore(b) - groupPopularityScore(a);
  });

  return sorted.map((group, idx) => ({
    group,
    visuals: getGroupVisuals(group, idx),
  }));
});

const hiddenPortalCardCount = computed(() => {
  if (isFilteringPortal.value || portalCardsExpanded.value) return 0;
  return Math.max(0, displayGroups.value.length - PORTAL_DEFAULT_VISIBLE_CARDS);
});

const visibleDisplayGroups = computed(() => {
  if (isFilteringPortal.value || portalCardsExpanded.value) {
    return displayGroups.value;
  }
  if (displayGroups.value.length <= PORTAL_DEFAULT_VISIBLE_CARDS) {
    return displayGroups.value;
  }
  return displayGroups.value.slice(0, PORTAL_DEFAULT_VISIBLE_CARDS);
});

const getGroupRefreshKey = (scope: GroupRefreshScope, uniqueId: string) => `${scope}:${uniqueId}`;

const isGroupRefreshOnCooldown = (scope: GroupRefreshScope, uniqueId: string): boolean => {
  void groupRefreshCooldownTick.value;
  const until = groupRefreshCooldownUntil.value[getGroupRefreshKey(scope, uniqueId)] || 0;
  return Date.now() < until;
};

const startGroupRefreshCooldown = (scope: GroupRefreshScope, uniqueId: string) => {
  const key = getGroupRefreshKey(scope, uniqueId);
  groupRefreshCooldownUntil.value = {
    ...groupRefreshCooldownUntil.value,
    [key]: Date.now() + GROUP_REFRESH_COOLDOWN_MS,
  };
  if (groupRefreshCooldownTimers[key]) {
    clearTimeout(groupRefreshCooldownTimers[key]);
  }
  groupRefreshCooldownTimers[key] = setTimeout(() => {
    delete groupRefreshCooldownTimers[key];
    groupRefreshCooldownTick.value += 1;
  }, GROUP_REFRESH_COOLDOWN_MS);
};

const clearGroupRefreshCooldowns = () => {
  for (const timer of Object.values(groupRefreshCooldownTimers)) {
    clearTimeout(timer);
  }
  for (const key of Object.keys(groupRefreshCooldownTimers)) {
    delete groupRefreshCooldownTimers[key];
  }
  groupRefreshCooldownUntil.value = {};
  groupRefreshCooldownTick.value += 1;
};

const isQuestionsRefreshDisabled = (group: DatasetCapabilityGroup): boolean => {
  const uniqueId = group.id || group.title;
  return !!refreshingGroupIds.value[uniqueId] || isGroupRefreshOnCooldown("questions", uniqueId);
};

const isFollowupsRefreshDisabled = (group: DatasetCapabilityGroup): boolean => {
  const uniqueId = group.id || group.title;
  return !!refreshingFollowupGroupIds.value[uniqueId] || isGroupRefreshOnCooldown("followups", uniqueId);
};

const collectGroupTables = (group: DatasetCapabilityGroup): string[] => {
  const tables: string[] = [];
  for (const related of group.related_data || []) {
    for (const table of related.tables || []) {
      const term = String(table || "").trim();
      if (term && !tables.includes(term)) {
        tables.push(term);
      }
    }
  }
  return tables;
};

const buildQuestionExclusions = (questions?: DatasetCapabilityQuestion[]) => {
  return (questions || [])
    .map((question) => ({
      label: String(question.label || "").trim(),
      query: String(question.query || "").trim(),
    }))
    .filter((question) => question.query);
};

const resolveRefreshEmptyReason = (responseData: any): string => {
  if (responseData?.code !== 200) return "";
  return String(responseData?.data?.refresh_disabled_reason || NO_MORE_UNIQUE_QUESTIONS_TIP).trim();
};

const handleRefreshClick = () => {
  if (refreshDisabled.value) return;
  isRefreshing.value = true;
  emit("refresh");
  if (refreshSafetyTimer) clearTimeout(refreshSafetyTimer);
  refreshSafetyTimer = setTimeout(() => {
    clearRefreshBusy();
  }, 120000);
};

watch(
  () => props.backgroundRefreshing,
  (busy, wasBusy) => {
    if (wasBusy && !busy) {
      clearRefreshBusy();
    }
  },
);

const handleRefreshGroupQuestions = async (group: DatasetCapabilityGroup) => {
  const uniqueId = group.id || group.title;
  if (refreshingGroupIds.value[uniqueId]) return;
  if (isGroupRefreshOnCooldown("questions", uniqueId)) {
    showToast("换一批太频繁，请稍后再试", "warning");
    return;
  }

  const tables = collectGroupTables(group);

  startGroupRefreshCooldown("questions", uniqueId);
  refreshingGroupIds.value[uniqueId] = true;
  try {
    const res = await axios.post("/api/v1/chat/dataset-menu/refresh-group-questions", {
      group_title: group.title,
      tables,
      dataset_menu_hash: props.payload.dataset_menu_hash,
      group_id: group.id || group.title,
      exclude_questions: buildQuestionExclusions(group.questions),
      purpose: "questions",
    });
    if (res.data?.code === 200 && res.data?.data?.questions?.length) {
      group.questions = res.data.data.questions;
    } else {
      const reason = resolveRefreshEmptyReason(res.data);
      if (reason) {
        showToast(reason, "warning");
        return;
      }
      console.warn("Invalid refresh questions response:", res.data);
      showToast("换一批推荐问题失败，请稍后重试", "error");
    }
  } catch (error) {
    console.error("Failed to refresh group questions:", error);
    showToast("换一批推荐问题失败，请稍后重试", "error");
  } finally {
    refreshingGroupIds.value[uniqueId] = false;
  }
};

const handleRefreshGroupFollowups = async (group: DatasetCapabilityGroup) => {
  const uniqueId = group.id || group.title;
  if (refreshingFollowupGroupIds.value[uniqueId]) return;
  if (isGroupRefreshOnCooldown("followups", uniqueId)) {
    showToast("换一批太频繁，请稍后再试", "warning");
    return;
  }

  const tables = collectGroupTables(group);
  startGroupRefreshCooldown("followups", uniqueId);
  refreshingFollowupGroupIds.value[uniqueId] = true;
  try {
    const res = await axios.post("/api/v1/chat/dataset-menu/refresh-group-questions", {
      group_title: group.title,
      tables,
      dataset_menu_hash: props.payload.dataset_menu_hash,
      group_id: group.id || group.title,
      exclude_questions: buildQuestionExclusions(group.followups),
      purpose: "followups",
    });
    if (res.data?.code === 200 && res.data?.data?.questions?.length) {
      group.followups = res.data.data.questions;
    } else {
      const reason = resolveRefreshEmptyReason(res.data);
      if (reason) {
        showToast(reason, "warning");
        return;
      }
      console.warn("Invalid refresh followups response:", res.data);
      showToast("换一批继续追问失败，请稍后重试", "error");
    }
  } catch (error) {
    console.error("Failed to refresh group followups:", error);
    showToast("换一批继续追问失败，请稍后重试", "error");
  } finally {
    refreshingFollowupGroupIds.value[uniqueId] = false;
  }
};

// 监听 payload 的变化重置刷新动画与过滤状态
watch(
  () => [props.payload?.dataset_menu_hash, props.payload?.generated_at] as const,
  (next, prev) => {
    if (!prev) return;
    const [nextHash, nextGeneratedAt] = next;
    const [prevHash, prevGeneratedAt] = prev;
    if (nextHash !== prevHash || nextGeneratedAt !== prevGeneratedAt) {
      clearRefreshBusy();
    }
    if (prevHash && nextHash !== prevHash) {
      searchQuery.value = "";
      selectedTag.value = "All";
    }
    pinnedTableDictionary.value = null;
    refreshingGroupIds.value = {};
    refreshingFollowupGroupIds.value = {};
    clearGroupRefreshCooldowns();
    tableRecommendState.value = {};
    portalCardsExpanded.value = false;
  },
);

watch(
  () => props.payload?.is_fallback,
  (isFallback, wasFallback) => {
    if (wasFallback === true && isFallback === false) {
      upgradedFromFallback.value = true;
      if (upgradedBannerTimer) clearTimeout(upgradedBannerTimer);
      upgradedBannerTimer = setTimeout(() => {
        upgradedFromFallback.value = false;
      }, 8000);
    }
  },
);

const toggleGroup = (groupId: string) => {
  expandedGroups.value[groupId] = !expandedGroups.value[groupId];
  // 展开状态变化后延迟保存到 Redis（1.2s debounce）
  savePortalPrefs();
};

const emitQuickQuestion = (query?: string, action: "send" | "fill" = "send") => {
  const text = String(query || "").trim();
  if (text) {
    emit("quick-question", text, action);
  }
};

const handleQuestionClick = (
  question: DatasetCapabilityQuestion,
  group: DatasetCapabilityGroup,
  action: "send" | "fill" = "send"
) => {
  const query = String(question.query || "").trim();
  if (!query) return;
  
  if (action === "send") {
    // 本地点击数备份（尾部取服务端最大值）
    localQuestionClicks.value[query] = (localQuestionClicks.value[query] || 0) + 1;
    savePortalPrefs(); // debounce 延迟保存
    emit("record-question-click", {
      query,
      label: question.label,
      group_id: group.id,
    });
  }
  emitQuickQuestion(query, action);
};

const handleFollowupClick = (
  followup: DatasetCapabilityQuestion,
  _group: DatasetCapabilityGroup,
  action: "send" | "fill" = "send"
) => {
  const query = String(followup.query || "").trim();
  if (!query) return;
  emitQuickQuestion(query, action);
};

const handleMetricClick = (metric: string, group: DatasetCapabilityGroup) => {
  const metricName = String(metric || "").trim();
  const sceneTitle = String(group.title || "").trim();
  if (!metricName || !sceneTitle) return;
  emitQuickQuestion(`查询${sceneTitle}最近6个月的${metricName}趋势`);
};

// 数据表数据字典快捷提问处理器
const handleQuickQuestionClick = (type: 'structure' | 'query', table: string, related: DatasetCapabilityRelatedData) => {
  const physicalName = related.table_physical_names?.[table] || "";
  const tableWithPhysical = physicalName ? `‘${table}’（物理表名：${physicalName}）` : `‘${table}’`;

  let queryText = "";
  if (type === 'structure') {
    queryText = `说明数据表${tableWithPhysical}的字段结构和分析口径`;
  } else if (type === 'query') {
    queryText = `查询数据表${tableWithPhysical}最近10条明细数据`;
  }
  
  if (queryText) {
    emitQuickQuestion(queryText);
  }
  
  // 收起浮窗
  pinnedTableDictionary.value = null;
};

const handleRecommendQuestions = async (
  group: DatasetCapabilityGroup,
  related: DatasetCapabilityRelatedData,
  table: string,
) => {
  const uniqueId = buildTableDictionaryKey(group, related, table);
  pinnedTableDictionary.value = uniqueId;
  tableRecommendState.value[uniqueId] = { loading: true, questions: [] };

  try {
    const columns = related.table_columns?.[table] || [];
    const res = await axios.post("/api/v1/chat/dataset-menu/recommend-table-questions", {
      table,
      physical_table_name: related.table_physical_names?.[table] || "",
      dataset_name: related.display_name || related.dataset || "",
      columns: columns.map((col) => ({
        name: col.name,
        term: col.term,
        type: col.type,
        description: col.description,
      })),
    });
    const questions = res.data?.data?.questions || [];
    if (!questions.length) {
      tableRecommendState.value[uniqueId] = {
        loading: false,
        questions: [],
        error: "暂未生成可用推荐提问，请稍后重试",
      };
      return;
    }
    tableRecommendState.value[uniqueId] = { loading: false, questions };
  } catch (error) {
    console.warn("Failed to recommend table questions", error);
    tableRecommendState.value[uniqueId] = {
      loading: false,
      questions: [],
      error: "生成推荐提问失败，请稍后重试",
    };
  }
};

const handleRecommendedQuestionClick = (
  question: { label: string; query: string },
  action: "send" | "fill" = "send"
) => {
  const query = String(question.query || "").trim();
  if (!query) return;
  emitQuickQuestion(query, action);
  pinnedTableDictionary.value = null;
};
</script>

<style scoped>
.portal-card-icon :deep(svg) {
  width: 18px;
  height: 18px;
  display: block;
  flex-shrink: 0;
}

.portal-card-icon[data-theme="blue"] {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  box-shadow: 0 4px 12px rgb(59 130 246 / 0.25);
}

.portal-card-icon[data-theme="violet"] {
  background: linear-gradient(135deg, #8b5cf6, #7c3aed);
  box-shadow: 0 4px 12px rgb(139 92 246 / 0.25);
}

.portal-card-icon[data-theme="emerald"] {
  background: linear-gradient(135deg, #10b981, #0d9488);
  box-shadow: 0 4px 12px rgb(16 185 129 / 0.25);
}

.portal-card-icon[data-theme="amber"] {
  background: linear-gradient(135deg, #f59e0b, #ea580c);
  box-shadow: 0 4px 12px rgb(245 158 11 / 0.25);
}

.portal-card-icon[data-theme="rose"] {
  background: linear-gradient(135deg, #f43f5e, #db2777);
  box-shadow: 0 4px 12px rgb(244 63 94 / 0.25);
}

.portal-card-icon[data-theme="cyan"] {
  background: linear-gradient(135deg, #06b6d4, #0284c7);
  box-shadow: 0 4px 12px rgb(6 182 212 / 0.25);
}

.portal-card-summary :deep(strong) {
  font-weight: 600;
  color: var(--summary-strong, rgb(55 65 81));
}

:global(.dark) .portal-card-summary :deep(strong) {
  filter: brightness(1.35);
}

@keyframes portal-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.portal-skeleton-shimmer {
  background: linear-gradient(90deg, rgb(243 244 246) 25%, rgb(229 231 235) 50%, rgb(243 244 246) 75%);
  background-size: 200% 100%;
  animation: portal-shimmer 1.4s ease-in-out infinite;
}

:global(.dark) .portal-skeleton-shimmer {
  background: linear-gradient(90deg, rgb(31 41 55) 25%, rgb(55 65 81) 50%, rgb(31 41 55) 75%);
  background-size: 200% 100%;
}

.animate-pulse-slow {
  animation: pulse 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: .85;
    transform: scale(0.98);
  }
}
</style>
