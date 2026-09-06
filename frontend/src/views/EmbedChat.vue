<template>
  <div
    class="flex h-full w-full max-w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-sans overflow-hidden relative min-w-0"
  >
    <!-- Sidebar (Desktop/Mobile) -->
    <ChatHistorySidebar
      v-model:visible="showHistorySidebar"
      v-model="historyKeyword"
      :loading="loadingHistory"
      :loading-more="loadingMoreHistory"
      :has-more="historyHasMore"
      :history-list="groupedHistoryList"
      active-trace-id=""
      @fetch-history="fetchHistory()"
      @load-more="fetchHistory(true)"
      @load-chat="handleHistoryClick"
      @open-full-logs="openTraceLogs"
      @delete-history="handleDeleteHistory"
      @delete-group="handleDeleteGroup"
      class="border-r border-gray-200 dark:border-gray-800"
    />

    <!-- Persistent Global Watermark (Fixed position, now in background) -->
    <div v-if="currentUser?.watermark ? currentUser.watermark.enabled : true" class="fixed inset-0 pointer-events-none overflow-hidden z-0 opacity-[0.04] select-none grid grid-cols-2 sm:grid-cols-3 gap-x-10 gap-y-24 p-10 justify-items-center items-center h-full w-full" aria-hidden="true">
        <div v-for="n in 60" :key="n" class="text-[10px] sm:text-xs font-black -rotate-[30deg] whitespace-nowrap uppercase tracking-tighter">
            <template v-if="currentUser?.watermark?.style === 'custom'">
                {{ currentUser?.watermark?.text || '南孜系统' }}
            </template>
            <template v-else>
                {{ currentUser?.real_name || currentUser?.user_name || 'Unauthorized' }}
            </template>
            {{ new Date().toLocaleDateString() }} {{ new Date().getHours() }}:{{ String(new Date().getMinutes()).padStart(2, '0') }}
        </div>
    </div>

    <div
      class="flex-1 flex flex-col h-full relative z-10 min-w-0 transition-[margin] duration-300 overflow-hidden w-full max-w-full"
      :style="pinnedDrawerMarginStyle"
    >
      <!-- Dynamic Header Status (New) -->
      <div
        class="h-12 border-b border-gray-100 dark:border-gray-800 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md px-4 flex items-center justify-between z-30 flex-shrink-0"
      >
        <div class="flex items-center space-x-3 overflow-hidden">
            <div class="flex flex-col min-w-0">
                <div class="flex items-center space-x-2">
                    <span class="text-sm font-black text-gray-800 dark:text-gray-100 truncate">
                        <template v-if="isProcessing">
                            {{ lastAgentMessage?.agentDisplayName || lastAgentMessage?.agentName || '智能体' }}
                        </template>
                        <template v-else-if="headerExpertLabel">
                            {{ headerExpertLabel }}
                        </template>
                        <template v-else>
                            {{ branding.default_agent_name || 'NanZi · AI' }}
                        </template>
                    </span>
                    <span v-if="isProcessing" class="flex h-1.5 w-1.5 relative">
                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                        <span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary"></span>
                    </span>
                    <span
                        v-else-if="headerExpertLabel"
                        class="inline-flex items-center px-1.5 py-0.5 rounded-full bg-primary/10 text-primary text-[9px] font-black uppercase tracking-wider shrink-0"
                    >
                        锁定
                    </span>
                </div>
                <div class="text-[10px] font-bold uppercase tracking-widest truncate flex items-center gap-1.5 min-w-0">
                    <template v-if="isProcessing">
                        <span class="text-gray-400">正在处理您的请求...</span>
                    </template>
                    <template v-else-if="headerExpertLabel">
                        <span class="text-gray-400 normal-case tracking-normal">准备就绪</span>
                        <button
                            v-if="!isRoutingSettingsLocked"
                            type="button"
                            @click.stop="switchToAuto"
                            class="text-gray-400 hover:text-red-500 normal-case tracking-normal font-bold transition-colors shrink-0"
                        >
                            退出
                        </button>
                    </template>
                    <template v-else>
                        <span class="text-gray-400">准备就绪</span>
                    </template>
                </div>
            </div>
        </div>

        <div class="flex items-center space-x-2">
            <!-- Fullscreen Button (desktop only) -->
            <button
                v-if="!isMobile"
                @click="toggleFullScreen"
                class="p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                :title="isFullScreen ? '退出全屏' : '全屏模式'"
            >
                <svg v-if="!isFullScreen" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                </svg>
                <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 14h6v6m0-6l-6 6m16-6h-6v6m0-6l6 6M4 10h6V4m0 6L4 4m16 6h-6V4m0 6l6-6" />
                </svg>
	            </button>
            <div class="relative">
              <button
                  v-if="isMobile || !config.showShortcuts"
                  @click="handleHeaderShortcutsClick"
                  class="p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                  :class="{ 'text-primary bg-primary/10': showShortcutsHint }"
                  :title="isMobile ? '快捷指令' : '显示快捷指令'"
              >
                  <CommandLineIcon class="h-4 w-4" aria-hidden="true" />
              </button>

              <!-- 折叠快捷指令后的右上角气泡引导提示 -->
              <transition
                enter-active-class="transition-all duration-300 ease-out"
                enter-from-class="opacity-0 -translate-y-2 scale-95"
                enter-to-class="opacity-100 translate-y-0 scale-100"
                leave-active-class="transition-all duration-200 ease-in"
                leave-from-class="opacity-100 translate-y-0 scale-100"
                leave-to-class="opacity-0 -translate-y-1 scale-95"
              >
                <div
                  v-if="showShortcutsHint && (isMobile || !config.showShortcuts)"
                  class="absolute right-0 top-full mt-2.5 z-50 flex items-center gap-2.5 whitespace-nowrap rounded-xl border border-primary/20 bg-primary/95 px-3 py-2 text-[12px] text-white shadow-2xl backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-900/95 pointer-events-auto"
                >
                  <!-- 顶部小尖角 -->
                  <div class="absolute -top-1.5 right-3 h-3 w-3 rotate-45 border-l border-t border-primary/20 bg-primary/95 dark:border-slate-700/60 dark:bg-slate-900/95" />

                  <CommandLineIcon class="h-4 w-4 shrink-0 text-white" aria-hidden="true" />
                  <div class="flex flex-col text-left">
                    <span class="font-bold leading-tight">已折叠快捷指令</span>
                    <span class="text-[11px] text-white/85 dark:text-slate-300 leading-tight mt-0.5">下次点这里重新打开</span>
                  </div>
                  <button
                    type="button"
                    @click.stop="dismissShortcutsHint"
                    class="ml-1 rounded-md p-1 text-white/70 hover:text-white hover:bg-white/20 dark:text-slate-400 dark:hover:text-white dark:hover:bg-slate-800 transition-colors"
                    title="知道了"
                  >
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </transition>
            </div>
            <button
                @click="showHelpModal = true"
                class="p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                title="查看帮助"
            >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
            </button>
            <button
                @click="showSettings = true"                class="p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                title="对话设置"
            >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            </button>
            <button
                @click="toggleBrowserPanel"
                class="relative p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                :class="browserPanelVisible ? 'text-primary bg-primary/10' : ''"
                title="打开服务端浏览器"
            >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2" stroke-width="1.8"/><path stroke-linecap="round" stroke-width="1.8" d="M3 8h18M7 6h.01M10 6h.01"/></svg>
                <span v-if="browserPanelVisible" class="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </button>
        </div>
      </div>

      <SessionResourceScopeBar
        v-if="resourceScope.project_name || resourceScopeCount > 0"
        :project-name="resourceScope.project_name"
        :resource-count="resourceScopeCount"
        :dataset-count="resourceScope.datasets.length"
        :knowledge-base-count="resourceScope.knowledge_bases.length"
        :skill-count="resourceScope.skills.length"
        :mcp-count="resourceScope.mcp_tools.length"
        @manage="openResourceScopeModal"
      />

      <!-- Main Chat Area -->
      <div
        class="flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-4 space-y-4 sm:space-y-6 w-full max-w-full min-w-0"
        ref="messagesContainer"
        @scroll="handleScroll"
      >
      <!-- No Permission Overlay -->
      <div
        v-if="!hasPermission"
        class="absolute inset-0 z-50 flex flex-col items-center justify-center bg-white dark:bg-gray-900 p-6 text-center"
      >
        <div class="p-4 bg-red-50 dark:bg-red-900/10 rounded-full mb-4">
          <svg
            class="w-12 h-12 text-red-500"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="1.5"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
        </div>
        <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-2">
          无访问权限
        </h3>
        <p
          class="text-sm text-gray-500 dark:text-gray-400 max-w-xs leading-relaxed"
        >
          认证失败，请检查您的账号是否有权限访问！
        </p>
      </div>
      <!-- URL agent_id deep-link error -->
      <div
        v-else-if="urlAgentAccessError"
        class="absolute inset-0 z-50 flex flex-col items-center justify-center bg-white dark:bg-gray-900 p-6 text-center"
      >
        <div
          class="p-4 rounded-full mb-4"
          :class="urlAgentAccessError.code === 'AGENT_FORBIDDEN' ? 'bg-amber-50 dark:bg-amber-900/10' : 'bg-red-50 dark:bg-red-900/10'"
        >
          <svg
            class="w-12 h-12"
            :class="urlAgentAccessError.code === 'AGENT_FORBIDDEN' ? 'text-amber-500' : 'text-red-500'"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="1.5"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
        </div>
        <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-2">
          {{ urlAgentAccessError.code === 'AGENT_FORBIDDEN' ? '无权使用该智能体' : '智能体不存在' }}
        </h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 max-w-sm leading-relaxed mb-2">
          {{ urlAgentAccessError.message }}
        </p>
        <p v-if="urlAgentAccessError.agentKey" class="text-xs text-gray-400 font-mono mb-4 break-all">
          agent_id={{ urlAgentAccessError.agentKey }}
          <template v-if="urlAgentAccessError.displayName">
            （{{ urlAgentAccessError.displayName }}）
          </template>
        </p>
        <p class="text-xs text-gray-400 max-w-sm leading-relaxed">
          {{ urlAgentAccessError.code === 'AGENT_FORBIDDEN'
            ? '请更换有权限的账号 / Token，或联系管理员开通该智能体。'
            : '请检查链接中的 agent_id 是否正确，或联系管理员确认智能体配置。' }}
        </p>
      </div>
      <!-- Connection Status Overlay -->
      <div
        v-if="connectionStatus !== 'connected'"
        class="fixed top-0 left-0 right-0 z-50 flex justify-center p-2"
      >
        <div
          class="px-3 py-1 rounded-full text-xs font-medium shadow-sm transition-colors duration-300"
          :class="{
            'bg-yellow-100 text-yellow-800':
              connectionStatus === 'reconnecting',
            'bg-red-100 text-red-800': connectionStatus === 'disconnected',
          }"
        >
          {{
            connectionStatus === "reconnecting" ? "正在重连..." : "连接已断开"
          }}
        </div>
      </div>
      <!-- Skeleton Loading State -->
      <div v-if="isInitialLoading" class="space-y-6">
        <div v-for="i in 3" :key="i" class="flex items-start space-x-3">
          <div
            class="w-8 h-8 rounded-full bg-gray-100 dark:bg-gray-800 animate-pulse"
          ></div>
          <div class="flex-1 space-y-2">
            <div
              class="h-4 bg-gray-100 dark:bg-gray-800 rounded w-3/4 animate-pulse"
            ></div>
            <div
              class="h-4 bg-gray-50 dark:bg-gray-800/50 rounded w-1/2 animate-pulse"
            ></div>
          </div>
        </div>
        <div class="flex flex-col items-center justify-center pt-8 animate-pulse">
            <span class="text-[10px] font-bold text-gray-300 uppercase tracking-widest mb-2">正在安全同步环境上下文</span>
            <div class="flex space-x-1">
                <div class="w-1 h-1 bg-gray-200 rounded-full animate-bounce"></div>
                <div class="w-1 h-1 bg-gray-200 rounded-full animate-bounce [animation-delay:0.2s]"></div>
                <div class="w-1 h-1 bg-gray-200 rounded-full animate-bounce [animation-delay:0.4s]"></div>
            </div>
        </div>
      </div>
      <!-- Welcome / Empty State (Smart Dashboard) -->
      <WelcomeDashboard
        v-else-if="messages.length === 0"
        :welcome-message="config.welcomeMessage"
        :slash-commands="effectiveSlashCommands"
        :welcome-cards="welcomeCards"
        :personal-resources="welcomePersonalResources"
        :personal-resources-refreshing="workbenchHomeRefreshing"
        @quick-question="handleQuickQuestion"
        @open-data-portal="openPortalDrawer"
        @open-personal-resources="openPersonalResources"
        @refresh-personal-resources="refreshWelcomePersonalResources"
        @select-knowledge-base="openKnowledgePortal"
        @open-workspace="openWorkspaceDrawer"
      />
      <!-- Start of Conversation Indicator -->
      <div v-if="!hasMoreHistory && messages.length > 0" class="w-full flex flex-col items-center py-8 opacity-60">
        <div class="w-8 h-8 rounded-full bg-gray-50 dark:bg-gray-800 flex items-center justify-center mb-2 border border-gray-100 dark:border-gray-700">
           <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
             <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 10l7-7m0 0l7 7m-7-7v18" />
           </svg>
        </div>
        <span class="text-[11px] font-bold text-gray-400 uppercase tracking-widest">这是您对话的起点</span>
        <div class="w-12 h-[1px] bg-gradient-to-r from-transparent via-gray-200 dark:via-gray-700 to-transparent mt-2"></div>
      </div>
      <!-- History Loading Indicator -->
      <div v-if="isLoadingHistory" class="w-full flex justify-center py-4 animate-fade-in-up">
        <div class="flex items-center gap-2 bg-gray-50/90 dark:bg-gray-800/90 px-4 py-1.5 rounded-full border border-gray-100 dark:border-gray-700 shadow-sm backdrop-blur-sm">
            <svg class="w-4 h-4 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-xs font-medium text-gray-500 dark:text-gray-400">正在加载历史记录...</span>
        </div>
      </div>
      <!-- Message List -->
      <div
        v-for="msg in displayMessages"
        :key="msg.id"
        class="flex flex-col space-y-4 animate-fade-in-up"
      >
        <!-- Time Label -->
        <div v-if="msg.isTimeLabel" class="flex justify-center py-2 animate-fade-in">
             <span class="text-[10px] font-medium text-gray-400 bg-gray-50 dark:bg-gray-800/80 px-2.5 py-0.5 rounded-full border border-gray-100 dark:border-gray-700 select-none">
                {{ msg.content }}
             </span>
        </div>
        <!-- User Message -->
        <div
          v-if="!msg.isTimeLabel && checkRole(msg, 'user')"
          class="flex flex-col group/msg relative"
        >
          <!-- Editing Mode -->
          <div v-if="editingMsgId === msg.id" class="flex flex-col items-end space-y-2 max-w-[90%] self-end">
             <textarea
                v-model="editContent"
                class="w-full p-3 border border-primary/30 rounded-lg shadow-sm focus:ring-2 focus:ring-primary focus:border-primary text-sm min-h-[80px] bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
              ></textarea>
              <div class="flex space-x-2">
                <button
                  @click="cancelEdit"
                  class="px-3 py-1 text-xs text-gray-500 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600 rounded"
                >取消</button>
                <button
                  @click="saveAndResend"
                  class="px-3 py-1 text-xs text-white bg-primary hover:opacity-90 rounded"
                  :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
                >发送</button>
              </div>
          </div>
          <!-- Normal Mode -->
          <div v-else>
            <div class="flex justify-end items-start space-x-2">
              <div
                class="max-w-[85%] text-white px-4 py-2.5 rounded-2xl rounded-tr-sm shadow-sm text-sm leading-relaxed transition-colors duration-300 relative"
                :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
              >
                <template v-for="parts in [splitUserMessageContent(msg.content)]" :key="'user-parts'">
                  <template v-if="parts.hasContext">
                    <div v-if="parts.userPart" class="whitespace-pre-wrap">{{ parts.userPart }}</div>
                    <div v-if="parts.userPart" class="my-2.5 border-t border-white/30" role="separator" />
                    <details class="group/sys mt-2 text-[10px] text-white/70 select-none">
                      <summary class="cursor-pointer hover:text-white flex items-center gap-1 font-semibold focus:outline-none list-none [&::-webkit-details-marker]:hidden">
                        <svg class="w-3 h-3 transform transition-transform duration-200 group-open/sys:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                        <span>⚙️ 附加系统元数据说明 (点击展开)</span>
                      </summary>
                      <div class="mt-1.5 p-2 rounded bg-black/15 text-white/85 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-all select-text selection:bg-white/20">
                        {{ parts.contextPart }}
                      </div>
                    </details>
                  </template>
                  <div v-else class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>

                <!-- Attached Files In Bubble -->
                <div v-if="msg.files && msg.files.length > 0" class="mt-2 space-y-2 border-t border-white/20 pt-2">
                    <div v-for="(file, fIdx) in msg.files" :key="fIdx" class="flex items-center bg-white/10 rounded-lg p-1.5 max-w-xs select-none">
                        <!-- Image Thumb -->
                        <AttachmentImageThumb
                          v-if="isImageFile(file)"
                          :file="file"
                          clickable
                          class="mr-2 border-white/10"
                          @click="(url) => handlePreviewImageUrl(url, file.filename)"
                        />
                        <!-- Skill Icon -->
                        <div v-else-if="file.type === 'skill'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2 font-mono">
                            ⚙️
                        </div>
                        <!-- Metadata Dataset Icon -->
                        <div v-else-if="file.type === 'metadata_dataset'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📊
                        </div>
                        <!-- Knowledge Base Icon -->
                        <div v-else-if="file.type === 'knowledge_base'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📚
                        </div>
                        <!-- Memory Icon -->
                        <div v-else-if="file.type === 'memory'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            🧠
                        </div>
                        <!-- File Icon -->
                        <div v-else class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📄
                        </div>
                        <div class="flex-1 min-w-0 flex flex-col">
                            <span v-if="file.type === 'skill' || file.type === 'knowledge_base' || file.type === 'metadata_dataset' || file.type === 'memory'" class="text-xs font-bold text-white truncate">{{ file.filename }}</span>
                            <template v-else>
                              <span v-if="canPreviewFile(file)" @click="handlePreviewFile(file)" class="text-xs font-bold text-white hover:underline cursor-pointer truncate">{{ file.filename }}</span>
                              <a v-else :href="resolveFileUrl(file.url)" target="_blank" class="text-xs font-bold text-white hover:underline truncate">{{ file.filename }}</a>
                            </template>
                            <span class="text-[9px] text-white/70 font-mono">
                                {{
                                    file.type === 'skill' ? '生态技能' :
                                    file.type === 'knowledge_base' ? '知识库' :
                                    file.type === 'metadata_dataset' ? '数据集' :
                                    file.type === 'memory' ? '记忆记录' :
                                    formatBytes(file.size)
                                }}
                            </span>
                        </div>
                    </div>
                </div>
              </div>
              <!-- User Avatar -->
              <div
                class="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-white shadow-sm overflow-hidden border border-white dark:border-gray-800"
                :style="{
                  background: config.userAvatar
                    ? `url(${config.userAvatar}) center/cover no-repeat`
                    : 'linear-gradient(135deg, #60a5fa, #3b82f6)',
                }"
              >
                <svg
                  v-if="!config.userAvatar"
                  class="w-5 h-5 opacity-90"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                  />
                </svg>
              </div>
            </div>
            <!-- User Message Actions Row -->
            <div
              class="flex justify-end items-center space-x-2 mt-1 pr-10"
            >
              <!-- Actions -->
              <div class="flex flex-nowrap items-center space-x-2">
              <button
                @click="startEdit(msg)"
                class="flex shrink-0 items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                title="编辑"
                :disabled="isProcessing"
              >
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                   <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
                <span class="hidden sm:inline">编辑</span>
              </button>
              <button
                @click="copyMessage(visibleStreamBody(msg))"
                class="flex shrink-0 items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                title="复制"
              >
                <svg
                  class="w-3 h-3"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                  />
                </svg>
                <span class="hidden sm:inline">复制</span>
              </button>
              <!-- Time -->
              <span v-if="msg.timestamp" class="text-[10px] text-gray-400 dark:text-gray-500 select-none ml-1">{{ formatBubbleTime(msg.timestamp) }}</span>
              </div>
            </div>
          </div>
        </div>
        <!-- System Message / Separator -->
        <div
          v-if="!msg.isTimeLabel && checkRole(msg, 'system')"
          class="w-full flex flex-col items-center justify-center my-6"
        >
          <span
            v-if="msg.timestamp"
            class="text-xs text-gray-400 dark:text-gray-500 font-medium tracking-wide mb-2"
            >{{ msg.timestamp }}</span
          >
          <div class="flex items-center space-x-2 opacity-60">
            <div class="h-px w-12 bg-gray-300 dark:bg-gray-600"></div>
            <span
              class="text-[10px] text-gray-400 dark:text-gray-500 font-medium tracking-wide"
              >{{ msg.content }}</span
            >
            <div class="h-px w-12 bg-gray-300 dark:bg-gray-600"></div>
          </div>
        </div>
        <!-- Agent Message -->
        <div v-if="!msg.isTimeLabel && checkRole(msg, 'agent')" class="flex justify-start items-start space-x-2 group/msg">
          <!-- Agent Avatar (Clickable for Settings) -->
          <div class="relative group">
            <!-- Pulse/Glow Effect -->
            <div
              class="absolute inset-0 rounded-full animate-pulse-slow transition-opacity"
              :class="(!msg.agentName || msg.isThinking) ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"
              :style="{
                backgroundColor: 'var(--primary-color, #1677ff)',
                filter: 'blur(4px)',
              }"
            ></div>
            <div
              class="relative w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-white shadow-sm overflow-hidden cursor-pointer hover:scale-110 hover:shadow-md active:scale-95 transition-all duration-200"
              @click.stop="showSettings = true; fetchAllowedAgents()"
              title="点击配置主题"
            >
              <img
                :src="agentAvatarUrl"
                class="w-full h-full object-cover"
                alt="NanZi AI agent"
              />
            </div>
            <!-- Tiny indicator dot to pulse when NOT hovered -->
            <div
              class="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-green-400 border-2 border-white dark:border-gray-900 rounded-full group-hover:hidden"
            >
              <div
                class="absolute inset-0 rounded-full animate-ping bg-green-400 opacity-75"
              ></div>
            </div>
          </div>
          <div class="w-full max-w-[90%] min-w-0">
            <!-- Agent Name (Smart Status Capsule) -->
            <div class="mb-1 ml-1 flex items-center">
              <div
                class="flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-medium transition-all duration-500 ease-out border"
                :class="msg.agentName || msg.agentDisplayName
                  ? 'bg-blue-50/80 border-blue-100 text-blue-700 dark:bg-blue-900/20 dark:border-blue-800 dark:text-blue-300 opacity-100 translate-y-0'
                  : msg.isThinking
                    ? 'bg-gray-50 border-gray-200 text-gray-500 dark:bg-gray-800/70 dark:border-gray-700 dark:text-gray-400 opacity-100 translate-y-0'
                  : 'opacity-0 translate-y-1 bg-transparent border-transparent'"
              >
                <!-- Text：调度占位（三点跳动）→ 智能体名（淡入轻微上滑） -->
                <Transition name="slide-fade" mode="out-in">
                  <span
                    v-if="msg.agentDisplayName || msg.agentName"
                    :key="`agent-${msg.agentDisplayName || msg.agentName}`"
                    class="inline-flex items-center space-x-1.5"
                  >
                    <span>{{ msg.agentDisplayName || msg.agentName }}</span>
                    <span v-if="msg.agentName" class="opacity-70 font-normal">{{ String(msg.agentName || '').startsWith('sys_') ? '· 系统指令' : '· 为您服务' }}</span>
                  </span>
                  <span v-else key="dispatch-placeholder" class="inline-flex items-center">
                    智能体正在分配调度中
                    <span class="inline-flex ml-0.5" aria-hidden="true">
                      <span class="animate-bounce-dot font-bold" style="animation-delay: 0s">.</span>
                      <span class="animate-bounce-dot font-bold" style="animation-delay: 0.15s">.</span>
                      <span class="animate-bounce-dot font-bold" style="animation-delay: 0.3s">.</span>
                    </span>
                  </span>
                </Transition>
              </div>
            </div>
            <div
              class="text-sm leading-relaxed transition-all duration-300 relative group/bubble"
              :class="[
                `markdown-theme-${config.markdownTheme || 'default'}`,
                { 'message-borderless': config.hideMessageBorder },
                visibleStreamBody(msg) || msg.groundingBlocked || msg.businessConfirmation || msg.userQuestion || (msg.processTimeline && msg.processTimeline.length > 0)
                  ? [
                      'px-4 py-3 rounded-2xl rounded-tl-sm shadow-none border border-gray-100 dark:border-gray-700 border-l-4 border-l-primary/60 dark:border-l-primary/40 min-h-[46px]',
                      msg.isThinking
                        ? 'bg-slate-50/80 dark:bg-slate-800/80'
                        : 'bg-white dark:bg-gray-800',
                    ]
                  : 'min-h-0 bg-transparent',
              ]"
            >
              <ReusableResultNotice
                v-if="msg.reusableResultStatus?.status === 'reused'"
                :origin-name="msg.reusableResultStatus.originName"
              />
              <ChatExecutionTimeline
                v-model="msg.isThoughtExpanded"
                :timeline="msg.processTimeline"
                :logs="msg.logs"
                :reasoning-content="msg.reasoningContent"
                :process-narration="msg.processNarration"
                :process-narration-pending="msg.processNarrationPending"
                :is-thinking="msg.isThinking"
                :has-answer="Boolean(visibleStreamBody(msg))"
                :thinking-text="msg.thinkingText"
                :duration="msg.thoughtDuration"
                :skill-summary="getSkillFlowBadgesForMessage(msg, messages).length > 0 ? summarizeSkillFlowBadges(getSkillFlowBadgesForMessage(msg, messages)) : ''"
                :skill-badges="getSkillFlowBadgesForMessage(msg, messages)"
                :suppress-permission-logs="Boolean(msg.pendingPermission)"
                dark-mode
              />
              <ToolPermissionCard
                v-if="msg.pendingPermission"
                :payload="msg.pendingPermission"
                @submit="(confirmed) => confirmPendingPermission(msg, confirmed)"
              />
              <!-- External Tool Execution -->
              <div
                v-if="msg.pendingExternalExecution"
                class="mt-3 rounded-lg border border-sky-200 dark:border-sky-900/50 bg-sky-50/80 dark:bg-sky-900/20 p-3 text-xs transition-all"
              >
                <div class="flex items-start gap-2">
                  <div class="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300">
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                  </div>
                  <div class="min-w-0 flex-1">
                    <!-- Card Header with Toggle -->
                    <div
                      class="flex items-center justify-between gap-2 cursor-pointer select-none group"
                      :title="(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) ? '点击收起' : '点击展开'"
                      @click="msg.pendingExternalExecution.expanded = !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending'))"
                    >
                      <div class="flex min-w-0 flex-1 items-center gap-2">
                        <div class="font-bold text-sky-900 dark:text-sky-100 shrink-0">
                          {{ msg.pendingExternalExecution.title || '外部工具执行' }}
                        </div>
                        <!-- Collapsed summary preview -->
                        <span
                          v-if="!(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) && (msg.pendingExternalExecution.tool_call?.name || msg.pendingExternalExecution.details)"
                          class="min-w-0 flex-1 truncate text-[11px] text-sky-800/70 dark:text-sky-200/70 font-normal"
                        >
                          {{ msg.pendingExternalExecution.tool_call?.name ? `${msg.pendingExternalExecution.tool_call.name}${msg.pendingExternalExecution.tool_call.args ? ': ' + JSON.stringify(msg.pendingExternalExecution.tool_call.args) : ''}` : msg.pendingExternalExecution.details }}
                        </span>
                      </div>
                      <div class="flex items-center gap-1.5 shrink-0">
                        <span class="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                          {{ formatExternalExecutionStatus(msg.pendingExternalExecution.status) }}
                        </span>
                        <!-- Fold/Unfold Arrow -->
                        <button
                          type="button"
                          class="flex h-5 w-5 items-center justify-center rounded text-sky-600 hover:text-sky-900 dark:text-sky-400 dark:hover:text-sky-200 transition-colors"
                          :aria-label="(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) ? '收起执行' : '展开执行'"
                          @click.stop="msg.pendingExternalExecution.expanded = !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending'))"
                        >
                          <svg
                            class="h-3.5 w-3.5 transition-transform duration-200"
                            :class="{ 'rotate-180': !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) }"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 15-7-7-7 7" />
                          </svg>
                        </button>
                      </div>
                    </div>

                    <!-- Expanded Content -->
                    <div v-show="msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')" class="mt-2">
                      <div class="text-sky-800/80 dark:text-sky-200/80 break-words">
                        {{ msg.pendingExternalExecution.details }}
                      </div>
                      <div
                        v-if="msg.pendingExternalExecution.tool_call?.name"
                        class="mt-2 rounded-md bg-white/70 dark:bg-gray-950/30 border border-sky-100 dark:border-sky-900/40 p-2 font-mono text-[10px] text-gray-600 dark:text-gray-300 overflow-x-auto max-h-56 overflow-y-auto"
                      >
                        <div class="font-semibold text-sky-900 dark:text-sky-200 mb-0.5">{{ msg.pendingExternalExecution.tool_call.name }}</div>
                        <pre v-if="msg.pendingExternalExecution.tool_call.args" class="whitespace-pre-wrap break-all font-mono">{{ JSON.stringify(msg.pendingExternalExecution.tool_call.args, null, 2) }}</pre>
                      </div>
                      <div v-if="msg.pendingExternalExecution.status === 'pending'" class="mt-3 space-y-2">
                        <textarea
                          v-model="msg.pendingExternalExecution.outputDraft"
                          rows="4"
                          placeholder="在此粘贴客户端执行该工具后的输出结果..."
                          class="w-full rounded-md border border-sky-200 dark:border-sky-800 bg-white/90 dark:bg-gray-950/40 px-3 py-2 text-xs text-gray-700 dark:text-gray-200"
                        />
                        <button
                          @click="submitPendingExternalExecution(msg)"
                          :disabled="msg.pendingExternalExecution.isSubmitting"
                          class="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          提交结果并继续
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <GroundingBlockedCard
                v-if="msg.groundingBlocked"
                class="mt-2"
                :payload="msg.groundingBlocked"
                :disabled="isProcessing"
                @action="(action) => handleGroundingAction(msg.groundingBlocked, action)"
              />
              <!-- Main Content -->
              <div v-if="visibleStreamBody(msg) && !msg.groundingBlocked" class="relative group/content mt-2">
                <!-- Floating Copy Button (Moved here to avoid overlap) -->
                <button
                  v-if="!msg.datasetNavigation?.groups?.length"
                  @click="copyMessage(visibleStreamBody(msg))"
                  class="absolute -top-1 -right-1 p-1.5 text-gray-400 bg-white/90 dark:bg-gray-700/90 hover:bg-gray-100 dark:hover:bg-gray-600 hover:text-primary rounded-md opacity-0 group-hover/content:opacity-100 transition-all z-10 shadow-sm border border-gray-100 dark:border-gray-600"
                  title="复制内容"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                </button>
                                                                <AgentHandoffNotice v-if="msg.agentHandoff" :handoff="msg.agentHandoff" />
                                                                <div
                                                                  v-if="msg.permissionNotice?.row_filter_applied && !msg.chatbiInsight"
                                                                  class="mb-2 inline-flex max-w-full items-start gap-1.5 rounded-lg border border-emerald-100 bg-emerald-50/70 px-2.5 py-1.5 text-[11px] font-medium leading-relaxed text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300"
                                                                >
                                                                  <svg class="mt-0.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                                                                  </svg>
                                                                  <span>{{ msg.permissionNotice.message || '已按你的数据权限自动过滤结果' }}</span>
                                                                </div>
                                                                <MessageRenderer
                                                                  v-if="!msg.groundingBlocked && !msg.datasetNavigation?.groups?.length"
                                                                  :content="visibleStreamBody(msg)"
                                                                  :theme="config.markdownTheme"
                                                                  :conversation-id="conversationId"
                                                                  :enable-browser-open="true"
                                                                  :quick-context="quickContextForMessage(msg)"
                                                                  :hide-quick-buttons="!!msg.businessConfirmation || !!msg.userQuestion"
                                                                  @quick-question="handleQuickQuestion"
                                                                  @show-citation="(payload) => handleShowCitation(msg, payload.id, payload.anchor)"
                                                                  @open-canvas="handleOpenCanvas"
                                                                  @open-browser-url="handleOpenWebPreviewUrl"
                                                                />
                                                                <ErrorDetailCard
                                                                  v-if="msg.errorDetail?.rawError"
                                                                  :raw-error="msg.errorDetail.rawError"
                                                                  :ai-status="msg.errorDetail.aiStatus"
                                                                />
                                                                <DatasetCapabilityMenu
                                                                  v-if="msg.datasetNavigation"
                                                                  :payload="msg.datasetNavigation"
                                                                  @quick-question="handleQuickQuestion"
                                                                  @record-question-click="(payload) => recordDatasetMenuQuestionClick(msg.datasetNavigation, payload)"
                                                                  @clear-question-click="(payload) => clearDatasetMenuQuestionClick(msg.datasetNavigation, payload)"
                                                                  @refresh="refreshDatasetMenuNavigation(msg)"
                                                                  @execute-saved-report="handleExecuteSavedReport"
                                                                />
                                <!-- Typewriter Cursor -->
                                <span
                                  v-if="isProcessing && msg.id === lastAgentMessage?.id && !msg.isThinking"
                                  class="inline-block w-1.5 h-4 ml-1 bg-primary/60 animate-pulse-fast align-middle rounded-sm"
                                  :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
                                                                ></span>
                                                              </div>

              <BusinessConfirmationCard
                v-if="msg.businessConfirmation"
                :payload="msg.businessConfirmation"
                :disabled="isProcessing"
                @submit="(payload) => submitBusinessConfirmation(msg, payload)"
              />

              <UserQuestionCard
                v-if="msg.userQuestion"
                :payload="msg.userQuestion"
                :disabled="isProcessing"
                @submit="(payload) => submitUserQuestion(msg, payload)"
              />

                                <!-- AI Stalled Thinking Prompt (Moved out to be sibling to msg.content) -->
                                <div
                                  v-if="isProcessing && msg.id === lastAgentMessage?.id && showStalledPrompt"
                                  class="mt-2"
                                >
                                  <span
                                    class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-blue-200/55 dark:bg-blue-900/55 border border-blue-200/80 dark:border-blue-900/50 text-[11px] text-blue-600 dark:text-blue-300 select-none animate-fade-in align-middle backdrop-blur-sm shadow-sm"
                                  >
                                    <svg class="w-3 h-3 text-blue-500 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
                                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"></circle>
                                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                    </svg>
                                    <span>AI 还在思考，请稍后</span>
                                    <span class="inline-flex space-x-0.5 ml-0.5">
                                      <span class="animate-bounce-dot text-blue-500 font-bold" style="animation-delay: 0s">.</span>
                                      <span class="animate-bounce-dot text-blue-500 font-bold" style="animation-delay: 0.15s">.</span>
                                      <span class="animate-bounce-dot text-blue-500 font-bold" style="animation-delay: 0.3s">.</span>
                                    </span>
                                  </span>
                                </div>
                                                            </div>
            <!-- Agent Message Actions (Overlay/Bottom) -->
            <ChatBIInsightPanel
              v-if="msg.chatbiInsight || (msg.citations && msg.citations.length)"
              :meta="msg.chatbiInsight"
              :citations="msg.citations"
              @open-citation="({ citation, event }) => openCitationPopover(citation, event)"
            />
            <ChatBIMetadataGuide v-if="msg.chatbiMetadataGuide" :guide="msg.chatbiMetadataGuide" @select="handleQuickQuestion" />
            <div
              v-if="!(isProcessing && msg.id === lastAgentMessage?.id)"
              class="flex min-w-0 max-w-full flex-nowrap items-center space-x-2 overflow-x-auto sm:overflow-x-visible mt-1 scrollbar-hide"
            >
              <!-- Time -->
              <span v-if="msg.timestamp" class="text-[10px] text-gray-400 dark:text-gray-500 select-none mr-1">{{ formatBubbleTime(msg.timestamp) }}</span>
              <button
                @click="copyMessage(visibleStreamBody(msg))"
                class="flex min-h-8 shrink-0 items-center space-x-1 text-[11px] text-gray-500 hover:text-primary transition-colors rounded px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-2 py-1'"
                title="复制"
              >
                <svg
                  class="w-3.5 h-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                  />
                </svg>
                <span class="hidden sm:inline">复制</span>
              </button>
              <MessageActionMenus
                mode="regenerate"
                :can-regenerate="msg === lastAgentMessage && !isProcessing"
                @regenerate="regenerate"
              />
              <div class="hidden sm:block shrink-0">
                <MessageActionMenus
                  mode="data"
                  :has-conversation-data-file="hasConversationDataFile"
                  :reusable-result-id="msg.reusableResultStatus?.resultId"
                  :has-conversation-reusable-result="hasConversationReusableResult"
                  :has-conversation-artifact="hasConversationArtifact"
                  :conversation-reusable-count="conversationReusableResultCount"
                  :conversation-artifact-count="conversationArtifactCount"
                  :reusable-count="currentMessageReusableCount(msg)"
                  :artifact-count="msg.trace_id ? artifactCount(msg.trace_id) : 0"
                  @open-reusable-results="openReusableResults(msg.reusableResultStatus?.resultId, msg.trace_id, msg.reusableResultStatus?.status)"
                  @open-artifacts="openMessageArtifacts(msg.trace_id)"
                />
              </div>
              <!-- Token 消耗：移动端仅 icon，桌面端展示 in/out 明细 -->
              <button
                v-if="msg.prompt_tokens !== undefined || msg.completion_tokens !== undefined"
                @click="openModelCallStats(msg)"
                class="flex sm:hidden shrink-0 items-center justify-center text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800 p-2.5"
                title="查看 Token 消耗详情"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h10M7 16h6M5 6a2 2 0 012-2h10a2 2 0 012 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V6z" />
                </svg>
              </button>
              <button
                v-if="msg.prompt_tokens !== undefined || msg.completion_tokens !== undefined"
                @click="openModelCallStats(msg)"
                class="hidden sm:flex shrink-0 items-center space-x-1.5 text-[10px] text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-800/40 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-primary dark:hover:text-primary-active border border-gray-100/50 dark:border-gray-800/20 rounded px-1.5 py-0.5 select-none font-mono transition-all duration-200 cursor-pointer active:scale-95 ml-1"
                title="点击查看详细的大模型调用统计指标（如单步耗时、工具调用明细、Token消耗详情等）"
              >
                <span class="flex items-center space-x-0.5">
                  <span class="scale-90 text-[9px] text-gray-400/80">in:</span>
                  <span class="font-medium text-gray-500 dark:text-gray-400">{{ msg.prompt_tokens || 0 }}</span>
                </span>
                <span class="text-gray-300 dark:text-gray-700">/</span>
                <span class="flex items-center space-x-0.5">
                  <span class="scale-90 text-[9px] text-gray-400/80">out:</span>
                  <span class="font-medium text-gray-500 dark:text-gray-400">{{ msg.completion_tokens || 0 }}</span>
                </span>
              </button>
              <!-- 反馈与 ChatBI 扩展操作（保持完整宽度，随操作栏滚动） -->
              <div class="flex shrink-0 items-center space-x-1">
                <template v-if="!hideEmbedLikeDislike">
                <button
                  @click="handleFeedback(msg, 'up')"
                  class="rounded transition-colors hover:bg-green-50 dark:hover:bg-green-900/20 text-gray-400 hover:text-green-500"
                  :class="[
                    msg.feedback === 'up' ? 'text-green-500 bg-green-50 dark:bg-green-900/20' : '',
                    windowWidth < 640 ? 'p-2.5' : 'p-2'
                  ]"
                  title="很有帮助"
                >
                  <svg
                    class="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M14 10h4.708C19.712 10 20.5 10.743 20.5 11.658c0 .354-.05.7-.145 1.03l-1.921 6.641C18.232 20.141 17.514 21 16.5 21H8.5c-1.105 0-2-.895-2-2v-8c0-.55.224-1.05.586-1.414l5-5c.381-.381 1-.381 1.381 0L14 5v5z"
                    />
                  </svg>
                </button>
                <button
                  @click="handleFeedback(msg, 'down')"
                  class="rounded transition-colors hover:bg-red-50 dark:hover:bg-red-900/20 text-gray-400 hover:text-red-500"
                  :class="[
                    msg.feedback === 'down' ? 'text-red-500 bg-red-50 dark:bg-red-900/20' : '',
                    windowWidth < 640 ? 'p-2.5' : 'p-2'
                  ]"
                  title="回答不准确"
                >
                  <svg
                    class="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M10 14H5.292C4.288 14 3.5 13.257 3.5 12.342c0-.354.05-.7.145-1.03l1.921-6.641C5.768 3.859 6.486 3 7.5 3h8c1.105 0 2 .895 2 2v8c0 .55-.224 1.05-.586 1.414l-5 5c-.381.381-1 .381-1.381 0L10 19v-5z"
                    />
                  </svg>
                </button>
                </template>
                <ChatBIContinueAnalysis
                  v-if="msg.chatbiInsight?.actions?.length && checkRole(msg, 'agent') && !msg.isThinking"
                  :actions="msg.chatbiInsight.actions"
                  :is-mobile="isMobile"
                  :result-id="msg.chatbiInsight.result_id"
                  @select="handleChatBIContinueSelect"
                  @action="(action) => handleChatBIResultAction(action, msg)"
                />
                <MessageContinueAnalysis
                  v-if="checkRole(msg, 'agent') && isGeneralAgentMessage(msg) && !msg.chatbiInsight?.actions?.length && !msg.isThinking && visibleStreamBody(msg)"
                  :is-mobile="isMobile"
                  @select="(query) => handleQuickQuestion(query, 'send', visibleStreamBody(msg))"
                />
                <MessageActionMenus
                  mode="more"
                  :show-data-on-mobile="true"
                  :has-conversation-data-file="hasConversationDataFile"
                  :reusable-result-id="msg.reusableResultStatus?.resultId"
                  :has-conversation-reusable-result="hasConversationReusableResult"
                  :has-conversation-artifact="hasConversationArtifact"
                  :conversation-reusable-count="conversationReusableResultCount"
                  :conversation-artifact-count="conversationArtifactCount"
                  :can-export="Boolean(msg.trace_id)"
                  :has-trace="Boolean(msg.trace_id)"
                  :reusable-count="currentMessageReusableCount(msg)"
                  :artifact-count="msg.trace_id ? artifactCount(msg.trace_id) : 0"
                  :has-token-stats="msg.prompt_tokens !== undefined || msg.completion_tokens !== undefined"
                  :can-save-report="canSaveGoldenReportFromMessage(msg) && checkRole(msg, 'agent') && !msg.isThinking"
                  @export-data="msg.trace_id && exportData(msg.trace_id, 'xlsx')"
                  @open-reusable-results="openReusableResults(msg.reusableResultStatus?.resultId, msg.trace_id, msg.reusableResultStatus?.status)"
                  @open-artifacts="openMessageArtifacts(msg.trace_id)"
                  @open-trace="msg.trace_id && openEmbedTrace(msg.trace_id)"
                  @open-stats="openModelCallStats(msg)"
                  @save-report="handleSaveReportFromMessage(msg)"
                />
              </div>
            </div>
            </div>
        </div>
      </div>
    </div>
    <!-- Floating Scroll Down Button (Refined) -->
    <transition
      enter-active-class="transition-all duration-500 cubic-bezier(0.34, 1.56, 0.64, 1)"
      enter-from-class="opacity-0 translate-y-10 scale-50"
      enter-to-class="opacity-100 translate-y-0 scale-100"
      leave-active-class="transition-all duration-300 ease-in"
      leave-to-class="opacity-0 translate-y-4 scale-90"
    >
      <div
        v-if="showNewMessageHint"
        class="absolute bottom-52 left-1/2 -translate-x-1/2 z-30"
      >        <button
          @click="scrollToBottom(true)"
          class="flex items-center space-x-2 px-4 py-2.5 bg-primary text-white shadow-2xl shadow-primary/40 rounded-full text-xs font-black hover:-translate-y-0.5 active:scale-95 transition-all group"
          :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
        >
          <svg class="w-4 h-4 animate-bounce" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 13l-7 7-7-7" /></svg>
          <span class="tracking-widest uppercase">查看最新消息</span>
          <div class="ml-1 w-1.5 h-1.5 rounded-full bg-white animate-pulse"></div>
        </button>
      </div>
    </transition>

    <CitationPopover
      :visible="citationPopover.visible"
      :citation="citationPopover.citation"
      :anchor-rect="citationPopover.anchorRect"
      :anchor-el="citationPopover.anchorEl"
      @close="closeCitationPopover"
      @copy="(content) => copyToClipboard(content)"
      @view-original="handleViewOriginal"
    />

    <RagPreviewDrawer
      v-model="ragPreviewVisible"
      :doc-name="ragPreviewDocName"
      :page-no="ragPreviewPageNo"
      :file-url="ragPreviewFileUrl"
      :content="ragPreviewContent"
      :is-office-document="isOfficeDocument"
    />

    <!-- Input Area -->
    <div v-if="hasPermission && !urlAgentAccessError" class="flex-shrink-0 bg-white dark:bg-gray-900 relative z-20">
      <div
        v-if="quotaBannerMessage"
        class="px-4 py-2 text-xs border-b"
        :class="quotaIsBlocked ? 'bg-rose-50 text-rose-800 border-rose-100' : 'bg-amber-50 text-amber-800 border-amber-100'"
      >
        {{ quotaBannerMessage }}
      </div>
      <div v-if="skillCreatedInfo" class="px-3 pt-2">
        <SkillCreatedBanner
          :info="skillCreatedInfo"
          @mount="mountCreatedSkill"
          @dismiss="skillCreatedInfo = null"
        />
      </div>
      <ChatInput
        ref="chatInputRef"
        v-model="userInput"
        :is-processing="isProcessing || remoteRunActive"
        :is-submitting="sendLocked"
        :show-shortcuts="!isMobile && config.showShortcuts"
        :slash-commands="effectiveSlashCommands"
        :allowed-agents="allowedAgents"
        :current-user="currentUser"
        :window-width="windowWidth"
        :approval-mode="config.approvalMode"
        :selected-model="config.overrideModel"
        :available-models="availableModels"
        :context-usage="contextUsage"
        :context-compaction-enabled="Boolean(conversationId)"
        :context-compaction-count="contextCompactionCount"
        :context-compaction-records="contextCompactions"
        :context-compaction-loading="contextCompactionsLoading"
        :context-compaction-error="contextCompactionsError"
        :context-compaction-action-loading="contextCompactionActionLoading"
        :thinking-enable-override="thinkingEnableOverride"
        :reasoning-effort-override="reasoningEffortOverride"
        :active-ltm-preference="activeLtmPreference"
        :agent-id="effectiveEmbedChatAgentId"
        :attached-mcp-tool-names="(resourceScope.mcp_tools || []).map((item: any) => String(item.name || '')).filter(Boolean)"
        :routing-mode="config.routingMode"
        :expert-agent-id="config.expertAgentId"
        :is-loading-agents="isLoadingAgents"
        :lock-expert-agent="isRoutingSettingsLocked"
        :docker-workspace-status="dockerWorkspaceStatus"
        :docker-workspace-container-id="dockerWorkspaceContainerId"
        :docker-workspace-started-at="dockerWorkspaceStartedAt"
        :docker-workspace-uptime-seconds="dockerWorkspaceUptimeSeconds"
        :docker-workspace-error="dockerWorkspaceError"
        @start-docker-workspace="ensureDockerWorkspace"
        @refresh-docker-workspace="refreshDockerWorkspaceStatus"
        @stop-docker-workspace="stopDockerWorkspace"
        @restart-docker-workspace="restartDockerWorkspace"
        @open-docker-terminal="openDockerTerminal"
        @update:approval-mode="(mode) => { config.approvalMode = mode; saveRoutingSettings(); }"
        @update:selected-model="handleEmbedModelSelection"
        @update:thinking-enable-override="thinkingEnableOverride = $event"
        @update:reasoning-effort-override="reasoningEffortOverride = $event"
        @send="sendMessage"
        @refresh-context-compactions="refreshEmbedContextCompactions(true)"
        @manual-context-compaction="manualCompactEmbedContext"
        @stop="stopGeneration"
        @toggle-shortcuts="toggleShortcuts"
        @open-command-manager="showAddModal = true"
        @upload-image="handleImageUpload"
        @edit-command="editCommand"
        @delete-command="confirmDeleteCommand"
        @switch-mode="handleSwitchMode"
        @switch-to-auto="switchToAuto"
        @switch-to-expert="switchToExpert"
        @refresh-agents="fetchAllowedAgents(true)"
        @reorder-commands="handleReorderCommands"
        @select-knowledge-base="openKnowledgePortal"
        @select-local-fs="openWorkspaceDrawer"
        @select-memory="openMemorySelector"
        @select-mcp-tool="mountMcpToolToSession"
        @system-command="handleSystemCommand"
        @ignore-ltm="handleIgnoreLtm"
        @dismiss-ltm="activeLtmPreference = null"
      >
        <template #banner>
          <div class="mx-3 mt-2">
            <div
              v-if="selectedReusableResultId"
              class="mb-2 flex items-center justify-between gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary dark:bg-primary/10"
            >
              <span class="truncate">已选择可复用结果，发送后将优先使用上一轮数据</span>
              <button
                type="button"
                class="shrink-0 font-semibold hover:underline"
                @click="selectedReusableResultId = null"
              >
                取消
              </button>
            </div>
            <div v-if="activeTodoTimeline">
              <ChatTodoCard :timeline="activeTodoTimeline" />
            </div>
            <div class="mt-2">
              <Transition name="bash-banner-fade">
                <DockerWorkspaceBanner
                  v-if="showDockerWorkspaceControl"
                  :workspace-status="dockerWorkspaceStatus"
                  :workspace-error="dockerWorkspaceError"
                  :container-id="dockerWorkspaceContainerId"
                  @start="ensureDockerWorkspace"
                  @refresh="refreshDockerWorkspaceStatus"
                  @close="dismissDockerWorkspaceBanner"
                />
              </Transition>
              <Transition name="bash-banner-fade">
                <BashEnvBanner
                  v-if="showBashBanner"
                  :env="bashBannerEnv!"
                  @dismiss="bashBannerDismissed = true"
                  @ignore="handleIgnoreBashBanner"
                />
              </Transition>
            </div>
          </div>
        </template>
      </ChatInput>
    </div>

    <ResourceScopeModal
      :visible="showResourceScopeModal"
      :draft="resourceScopeModalDraft"
      :groups="resourceOptionGroups"
      :active-tab="resourceScopeActiveTab"
      :orphan-count="modalResourceOrphanCount"
      :loading="resourceOptionsLoading"
      :saving="resourceScopeSaving"
      :option-search="resourceOptionSearch"
      :selected-count="modalSelectedCount"
      :option-total-count="modalOptionTotalCount"
      :skill-scope-selected-count="modalSkillScopeSelectedCount"
      :skill-scope-total-count="modalSkillScopeTotalCount"
      :orphan-selections="modalOrphanSelections"
      :selected-chips="modalSelectedChips"
      :sorted-options="sortedModalResourceOptions"
      :option-selected="resourceModalOptionSelected"
      :option-initial="resourceOptionInitial"
      :option-accent="resourceOptionAccent"
      @close="closeResourceScopeModal"
      @refresh="refreshResourceOptions"
      @save="saveResourceScope"
      @update:active-tab="resourceScopeActiveTab = $event"
      @remove-draft="removeModalDraftResource"
      @toggle-option="toggleModalResourceOption"
      @toggle-group="toggleModalResourceGroup"
    />

    <ChatCanvas
      :visible="canvasVisible"
      v-model:pinned="canvasPinned"
      v-model:canvas-width="canvasPinnedWidthReactive"
      :data="canvasData"
      :overlay="canvasFromWorkspace"
      :dock-side="canvasFromWorkspace ? 'left' : 'right'"
      :adjacent-dock-width="canvasFromWorkspace && showWorkspaceDrawer && workspacePinned ? workspaceDrawerWidthReactive : 0"
      :conversation-id="conversationId"
      @close="closeCanvas"
      @analyze-diff="handleAnalyzeDiff"
      @analyze-output="handleAnalyzeCodeOutput"
      @content-saved="handleWorkspaceContentSaved"
    />
    </div> <!-- Closing div for .flex-1.flex.flex-col -->

    <KnowledgePortalDrawer
      v-model="showKnowledgePortal"
      v-model:pinned="knowledgePinned"
      v-model:drawer-width="knowledgeDrawerWidthReactive"
      v-model:keep-open-on-question="knowledgeKeepOpenOnQuestion"
      v-model:hallucination-check="hallucinationCheckEnabled"
      v-model:similarity-threshold="knowledgeSimilarityThreshold"
      v-model:vector-weight="knowledgeVectorWeight"
      v-model:metadata-top-k="knowledgeMetadataTopK"
      :generated-at="knowledgeGeneratedAt"
      :project-resource-scope="projectSessionHasKnowledgeScope ? '仅显示当前项目会话已挂载的知识库' : ''"
      :datasets="scopedKnowledgeDatasets"
      :active-dataset-ids="scopedActiveDatasetIds"
      :recommendations="datasetRecommendations"
      :pinned-dataset-ids="pinnedDatasetIds"
      :dataset-documents="datasetDocuments"
      :document-recommendations="documentRecommendations"
      :loading="loadingKnowledgeDatasets"
      :load-error="knowledgeLoadError"
      @toggle-active="(id) => toggleDatasetActive(id, chatInputRef)"
      @load-recommendations="fetchRecommendations"
      @quick-question="handleQuickQuestion"
      @refresh="fetchDatasets"
      @toggle-pin="toggleDatasetPinned"
      @load-documents="fetchDatasetDocuments"
      @load-document-recommendations="fetchDocumentRecommendations"
    />

    <WorkspaceBrowserDrawer
      ref="workspaceDrawerRef"
      v-model="showWorkspaceDrawer"
      v-model:keep-open-on-select="workspaceKeepOpenOnSelect"
      v-model:pinned="workspacePinned"
      v-model:drawer-width="workspaceDrawerWidthReactive"
      :pinned-dock-class="workspacePinnedDockClass"
      :conversation-id="conversationId"
      :session-started="messages.length > 0"
      @select="handleSelectLocalFs"
      @preview="handleWorkspaceFilePreview"
    />

    <MyArtifactsDrawer
      v-model="showMyArtifactsDrawer"
      :conversation-id="conversationId"
      :trace-id="focusedOutputTraceId"
      :initial-tab="myArtifactsInitialTab"
      :selected-result-id="selectedReusableResultId"
      :focused-result-id="focusedReusableResultId"
      :reused-result-id="reusedReusableResultId"
      @select-reusable-result="selectReusableResult"
    />

    <MemoryBrowserDrawer
      v-model="showMemoryDrawer"
      v-model:keep-open-on-select="memoryKeepOpenOnSelect"
      v-model:pinned="memoryPinned"
      :pinned-dock-class="memoryPinnedDockClass"
      :attached-conversation-ids="attachedMemoryConversationIds"
      @mount="handleMemoryMount"
      @cleared="handleMemoryCleared"
    />

    <div
      v-if="showTraceModal"
      class="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm sm:p-4"
      @click.self.stop="closeTraceModal"
    >
        <div
            class="bg-white dark:bg-gray-800 w-full flex flex-col overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700 shadow-2xl transition-all duration-300"
            :class="windowWidth < 640 ? 'h-full rounded-none' : 'max-w-3xl h-[80vh] rounded-xl'"
        >
            <!-- Header -->
            <div
                class="px-4 py-3 sm:px-6 sm:py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0"
                :class="windowWidth < 640 ? 'justify-center relative' : 'justify-between'"
            >
                <div
                    class="flex items-center gap-2 sm:gap-3 min-w-0"
                    :class="windowWidth < 640 ? 'flex-col gap-0.5' : ''"
                >
                    <!-- Watermark Number -->
                    <div
                        v-if="activeHistoryIndex >= 0"
                        class="flex items-center justify-center w-5 h-5 rounded-full border flex-shrink-0 text-[9px] font-black select-none pointer-events-none -rotate-12 opacity-80"
                        :style="{ color: `hsl(${(activeHistoryIndex * 137.5) % 360}, 70%, 50%)`, borderColor: `hsl(${(activeHistoryIndex * 137.5) % 360}, 70%, 40%, 0.3)` }"
                    >
                        {{ activeHistoryIndex + 1 }}
                    </div>

                    <h3 class="text-sm sm:text-lg font-black text-gray-800 dark:text-gray-100 truncate">会话回溯详情</h3>
                    <span v-if="traceLogData?.history?.created_at" class="text-[9px] sm:text-xs text-gray-400 font-mono bg-white dark:bg-gray-700 px-1.5 py-0.5 rounded border border-gray-100 dark:border-gray-600 flex-shrink-0">
                        {{ formatDate(traceLogData.history.created_at).split(' ')[0] }}
                    </span>
                </div>
                <div
                    class="flex items-center gap-1 sm:gap-2 flex-shrink-0"
                    :class="windowWidth < 640 ? 'absolute right-3' : ''"
                >
                    <button
                        v-if="traceLogData"
                        @click.stop="continueChatFromTrace"
                        class="flex items-center space-x-1.5 px-3 py-1.5 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-lg transition-all text-xs font-black border border-primary/20"
                        title="加载此会话并继续聊天"
                    >
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                        </svg>
                        <span>继续聊天</span>
                    </button>

                    <div v-if="traceLogData" class="w-px h-4 bg-gray-300 dark:bg-gray-600 mx-1"></div>

                    <button
                        @click.stop="openDeleteModal(traceLogData?.trace_id)"
                        class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg transition-colors"
                        title="删除此记录"
                    >
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                    </button>

                    <div class="w-px h-4 bg-gray-300 dark:bg-gray-600 mx-1"></div>

                    <button
                         @click.stop="closeTraceModal"
                         class="rounded-full transition-colors flex items-center justify-center bg-gray-100 dark:bg-gray-700 text-gray-500"
                         :class="windowWidth < 640 ? 'w-8 h-8' : 'p-2 hover:text-gray-800 dark:text-gray-400 dark:hover:text-white bg-transparent dark:bg-transparent'"
                    >
                        <svg class="w-5 h-5 sm:w-6 sm:h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>

                    <!-- Back Button Helper for Mobile (Left side) -->
                    <button
                         v-if="windowWidth < 640"
                         @click.stop="closeTraceModal"
                         class="absolute left-[calc(-100vw+60px)] top-1/2 -translate-y-1/2 p-2"
                    >
                       <!-- Invisible hit area extension if needed, or just rely on top right close -->
                    </button>
                </div>
            </div>
            <!-- Content -->
            <div class="flex-1 overflow-y-auto p-4 sm:p-6 bg-gray-50 dark:bg-gray-900 custom-scrollbar">
                <div v-if="loadingTrace" class="flex flex-col items-center justify-center h-full text-gray-400 py-20">
                    <svg class="w-10 h-10 animate-spin mb-3 text-primary" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <p class="text-xs font-bold uppercase tracking-widest">正在安全同步执行链路</p>
                </div>
                <div v-else-if="traceLogData" class="space-y-4 sm:space-y-6 pb-10">
                    <!-- Conversation Thread Thread -->
                    <div v-if="conversationTurns.length > 0" class="space-y-6">
                        <div v-for="(turn, tIdx) in conversationTurns" :key="turn.id"
                             class="bg-white dark:bg-gray-800 p-4 rounded-3xl border border-gray-200 dark:border-gray-700 shadow-sm relative overflow-hidden"
                             :class="{'ring-2 ring-primary/20': turn.trace_id === traceLogData.trace_id}"
                        >
                            <!-- Turn Header -->
                            <div class="flex justify-between items-center mb-4">
                                <span class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center gap-1.5">
                                    <span class="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500/20"></span>
                                    对话回合 #{{ tIdx + 1 }}
                                </span>
                                <span class="text-[9px] text-gray-400 font-mono bg-gray-50 dark:bg-gray-700/50 px-2 py-0.5 rounded-full">{{ formatDate(turn.created_at) }}</span>
                            </div>

                            <!-- Q&A Content -->
                            <div class="space-y-4">
                                <div>
                                    <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2 opacity-70">提问 · Query</div>
                                    <div class="text-gray-800 dark:text-gray-200 text-sm font-bold leading-relaxed whitespace-pre-wrap bg-gray-50/50 dark:bg-gray-900/30 p-3 rounded-xl border border-gray-100 dark:border-gray-800">
                                        {{ turn.query || 'N/A' }}
                                    </div>
                                </div>
                                <div>
                                    <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2 opacity-70">回答 · Response</div>
                                    <div class="text-gray-600 dark:text-gray-300 text-xs sm:text-sm leading-relaxed">
                                        <MessageRenderer
                                          :content="stripInternalContextBlocks(turn.summary || 'N/A')"
                                          :theme="config.markdownTheme"
                                          :conversation-id="conversationId"
                                          :enable-browser-open="true"
                                          @open-browser-url="handleOpenWebPreviewUrl"
                                        />
                                    </div>
                                </div>
                            </div>

                            <!-- Embedded Thinking Chain (Steps) -->
                            <div class="mt-6 pt-4 border-t border-gray-50 dark:border-gray-700/50">
                                <button
                                    @click="toggleTurnSteps(turn)"
                                    class="flex items-center justify-between w-full p-2.5 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-all group/btn"
                                >
                                    <div class="flex items-center space-x-2 text-[11px] font-black text-gray-500 group-hover/btn:text-primary transition-colors uppercase tracking-widest">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                                        <span>执行全链路 (Steps)</span>
                                        <span v-if="turn.steps?.length" class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-[9px] font-mono">{{ turn.steps.length }}</span>
                                    </div>
                                    <div class="flex items-center">
                                        <div v-if="turn.loading" class="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2"></div>
                                        <svg
                                            class="w-4 h-4 text-gray-400 transform transition-transform duration-300"
                                            :class="{ 'rotate-180': turn.isExpanded }"
                                            fill="none" stroke="currentColor" viewBox="0 0 24 24"
                                        >
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                                        </svg>
                                    </div>
                                </button>

                                <!-- Steps List -->
                                <div v-show="turn.isExpanded" class="mt-4 space-y-4 pl-4 border-l-2 border-gray-100 dark:border-gray-700/50 animate-fade-in">
                                    <div v-if="turn.steps && turn.steps.length > 0" class="space-y-4">
                                        <div v-for="(step, sIdx) in turn.steps" :key="sIdx" class="relative group/step">
                                            <!-- Step Dot -->
                                            <div class="absolute -left-[26px] top-1 w-5 h-5 rounded-full border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-center text-[9px] font-black text-white z-10"
                                                :class="step.status === 'error' ? 'bg-amber-500' : 'bg-blue-500'">
                                                {{ Number(sIdx) + 1 }}
                                            </div>
                                            <!-- Step Card -->
                                            <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-3 shadow-sm">
                                                <div class="flex justify-between items-center mb-2">
                                                    <div class="flex items-center gap-2">
                                                        <span class="text-[9px] font-black px-1.5 py-0.5 rounded uppercase tracking-tighter"
                                                            :class="step.event_type === 'thought' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'">
                                                            {{ step.event_type }}
                                                        </span>
                                                        <span v-if="step.tool_name" class="text-[9px] font-black text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded">
                                                            {{ step.tool_name }}
                                                        </span>
                                                    </div>
                                                    <span class="text-[8px] text-gray-400 font-mono">{{ step.execution_time_ms ? `${step.execution_time_ms.toFixed(0)}ms` : '' }}</span>
                                                </div>
                                                <div class="space-y-2">
                                                    <pre v-if="step.tool_input" class="bg-gray-50 dark:bg-gray-900 p-2 rounded-lg text-[9px] text-gray-500 overflow-x-auto font-mono border border-gray-100 dark:border-gray-800">{{ typeof step.tool_input === 'string' ? step.tool_input : JSON.stringify(step.tool_input, null, 2) }}</pre>
                                                    <div v-if="step.tool_output && step.tool_output.content" class="text-[11px] text-gray-600 dark:text-gray-300 leading-relaxed bg-blue-50/20 p-2 rounded-lg border border-blue-100/10">
                                                        {{ step.tool_output.content }}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div v-else-if="!turn.loading" class="py-4 text-center text-[10px] text-gray-400 italic">
                                        暂无详细执行记录
                                    </div>
                                </div>
                            </div>

                            <!-- Indicator for current trace -->
                            <div v-if="turn.trace_id === traceLogData.trace_id" class="absolute top-0 right-0">
                                <div class="bg-primary text-white text-[8px] font-black px-2.5 py-1 rounded-bl-xl uppercase tracking-tighter shadow-sm animate-pulse">
                                    Current Turn
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-else class="text-center py-16 text-gray-400 bg-white dark:bg-gray-800 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700">
                    <div class="mb-2 opacity-20"><svg class="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg></div>
                    <p class="text-xs font-bold uppercase tracking-tighter">暂无详细执行日志回溯</p>
                </div>
            </div>
        </div>
    </div>
    <!-- Delete Group Confirmation Modal -->
    <ConfirmModal
      v-if="showDeleteGroupModal"
      title="一键删除分组会话"
      :message="`确定要一键删除“${groupToDelete?.title}”分组下的这 ${groupToDelete?.items?.length || 0} 个会话吗？此操作无法撤销。`"
      type="danger"
      @confirm="confirmDeleteGroup"
      @cancel="showDeleteGroupModal = false"
    />
    <!-- Delete Confirmation Modal -->
    <ConfirmModal
      v-if="showDeleteModal"
      title="删除历史记录"
      message="确定要删除这条对话记录吗？此操作无法撤销。"
      type="danger"
      @confirm="confirmDeleteTrace"
      @cancel="showDeleteModal = false"
    />
    <!-- Delete Command Confirmation Modal -->
    <ConfirmModal
      v-if="showDeleteCommandModal"
      title="删除快捷指令"
      :message="`确定要删除指令 [${commandToDelete?.label}] 吗？`"
      type="danger"
      @confirm="executeDeleteCommand"
      @cancel="showDeleteCommandModal = false"
    />
    <!-- Clear Session Confirmation Modal -->
    <ConfirmModal
      v-if="showConfirmModal"
      title="确定要开始新对话吗？"
      message="当前内容将保存至历史记录。"
      type="primary"
      @confirm="handleConfirmClearSession"
      @cancel="showConfirmModal = false"
    />
    <!-- Docker Workspace Terminal Modal -->
    <DockerTerminalModal
      :show="showDockerTerminal"
      :container-id="dockerWorkspaceContainerId"
      :conversation-id="conversationId"
      :auth-token="config.token"
      @close="showDockerTerminal = false"
    />
    <!-- Settings Modal -->
    <ChatSettings
      v-model:visible="showSettings"
      :config="config"
      :allowed-agents="allowedAgents"
      :routing-locked="isRoutingSettingsLocked"
      @set-theme="setTheme"
      @set-color="setColor"
      @mode-change="onModeChange"
      @switch-to-auto="switchToAuto"
      @switch-to-expert="switchToExpert"
      @save-settings="saveRoutingSettings"
      @reset-session="resetSession"
    />
    <PersonalResourcesModal
      v-model:visible="showPersonalResources"
      v-model:active-tab="personalResourcesTab"
      @open-report="handlePersonalResourceOpenReport"
      @open-conversation="handlePersonalResourceOpenConversation"
      @open-question="(payload) => {
        if (!payload || typeof payload !== 'object') return;
        const question = payload as { query?: unknown; action?: unknown };
        if (typeof question.query !== 'string') return;
        handlePersonalResourceOpenQuestion({
          query: question.query,
          action: question.action === 'fill' ? 'fill' : 'send',
        });
      }"
    />
    <!-- Embed 内独立站内消息弹层：iframe / 外部集成不依赖 Dashboard 顶栏铃铛 -->
    <PortalNotificationBell
      ref="portalInboxRef"
      variant="modal"
      :listen-global-event="false"
      @open-saved-report="handleInboxOpenSavedReport"
    />
    <DataPortalReportCreateModal
      :visible="showSaveReportModal"
      :report="saveReportForm"
      :overlay-class="saveReportModalOverlayClass"
      :overlay-style="saveReportModalOverlayStyle"
      scrollbar-variant="embed"
      @close="closeSavedReportEditor"
      @created="handleSavedReportEditorCreated"
    />

    <SavedReportRunModal
      :visible="showReportRunModal"
      :pending-report="pendingSavedReport"
      :form="reportRunForm"
      :previewing="isPreviewingSavedReport"
      :preview="reportRunPreview"
      :uses-month-range="savedReportUsesMonthRange"
      :uses-date-range="savedReportUsesDateRange"
      :overlay-class="saveReportModalOverlayClass"
      :overlay-style="saveReportModalOverlayStyle"
      @close="showReportRunModal = false"
      @execute="executeSavedReportWithOptions"
    />

    <!-- Modal: Help Guide -->
    <div
      v-if="showHelpModal"
      class="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      @click.self="showHelpModal = false"
    >
      <div
        class="bg-white dark:bg-gray-800 w-full max-w-lg rounded-2xl shadow-2xl overflow-hidden flex flex-col border border-gray-100 dark:border-gray-700 animate-fade-in-up"
        :class="windowWidth < 640 ? 'h-[80vh]' : 'max-h-[85vh]'"
      >
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
          <div class="flex items-center space-x-2">
            <div class="p-1.5 bg-primary/10 rounded-lg text-primary">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 class="text-base font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">使用指南</h3>
          </div>
          <button @click="showHelpModal = false" class="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto p-6 space-y-8 custom-scrollbar">
          <!-- Intro -->
          <section>
            <h4 class="text-xs font-black text-primary uppercase tracking-widest mb-3 flex items-center">
               <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
               系统简介
            </h4>
            <p class="text-sm text-gray-600 dark:text-gray-400 leading-relaxed font-medium">
              🚀 欢迎使用<b>本智能体平台</b>。本系统是一个集成多模型能力的 🤖 AI 助手，旨在通过自然语言交互，帮助您高效完成<b>通用咨询问答</b>、📊 数据查询分析、📚 私有文档检索及 ⚙️ 复杂业务流程处理。
            </p>
          </section>

          <!-- Interaction -->
          <section>
            <h4 class="text-xs font-black text-primary uppercase tracking-widest mb-4 flex items-center">
               <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
               如何交互
            </h4>
            <div class="grid grid-cols-1 gap-3">
               <div class="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-xl border border-gray-100 dark:border-gray-800 flex items-start space-x-3">
                  <div class="px-2 py-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-xs font-mono text-blue-500 font-bold">/</div>
                  <div>
                    <div class="text-[11px] font-black text-gray-800 dark:text-gray-200 uppercase mb-0.5">快捷指令</div>
                    <p class="text-[10px] text-gray-500 mb-2">Web 端支持<b>直接点击</b>快捷按钮；移动端输入斜杠 <span class="font-mono text-blue-500">/</span> 即可快速唤起。</p>
                    <p class="text-[10px] text-gray-500 mb-2"><span class="font-mono text-blue-500">{{ DATASET_PORTAL_SLASH_COMMAND }}</span> 会基于与 ChatBI 相同的数据集目录，由 AI 生成我的数据门户与可点击追问按钮。</p>
                    <div class="flex flex-wrap gap-1.5">
                      <span class="px-1.5 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[9px] rounded border border-blue-100 dark:border-blue-800 font-medium">{{ DATASET_PORTAL_SLASH_COMMAND }}</span>
                      <span class="px-1.5 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[9px] rounded border border-blue-100 dark:border-blue-800 font-medium">/new</span>
                      <span class="px-1.5 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[9px] rounded border border-blue-100 dark:border-blue-800 font-medium">/history</span>
                    </div>
                  </div>
               </div>
               <div class="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-xl border border-gray-100 dark:border-gray-800 flex items-start space-x-3">
                  <div class="px-2 py-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-xs font-mono text-purple-500 font-bold">@</div>
                  <div>
                    <div class="text-[11px] font-black text-gray-800 dark:text-gray-200 uppercase mb-0.5">提及专家</div>
                    <p class="text-[10px] text-gray-500 mb-2">Web 端可<b>从专家列表点击</b>指定；移动端输入艾特符号 <span class="font-mono text-purple-500">@</span> 即可指定专业智能体。</p>
                    <div class="flex flex-wrap gap-1.5">
                      <span class="px-1.5 py-0.5 bg-purple-50 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 text-[9px] rounded border border-purple-100 dark:border-purple-800 font-medium">@运维专家</span>
                      <span class="px-1.5 py-0.5 bg-purple-50 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 text-[9px] rounded border border-purple-100 dark:border-purple-800 font-medium">@数据分析</span>
                    </div>
                  </div>
               </div>
            </div>
          </section>

          <!-- Features -->
          <section>
            <h4 class="text-xs font-black text-primary uppercase tracking-widest mb-4 flex items-center">
               <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
               支持功能与示例 (点击可复制)
            </h4>
            <div class="space-y-4">
               <div class="group">
                  <div class="flex items-start space-x-3 mb-2">
                    <div class="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg text-indigo-500">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
                    </div>
                    <div>
                      <div class="text-sm font-bold text-gray-800 dark:text-gray-200">通用聊天问答</div>
                      <p class="text-[11px] text-gray-500 leading-relaxed">具备强大的语言理解能力，支持日常答疑、方案编写及代码建议。</p>
                    </div>
                  </div>
                  <div
                    class="ml-9 p-2.5 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-dashed border-gray-200 dark:border-gray-700 relative group/item cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-all"
                  >
                    <p class="text-[10px] text-gray-600 dark:text-gray-400 italic pr-12" @click="copyToClipboard('帮我写一个关于机房节能降耗的宣传文案。', 'help_chat')">“帮我写一个关于机房节能降耗的宣传文案。”</p>
                    <div class="absolute right-2 top-2.5 flex items-center space-x-2">
                      <button
                        @click="handleQuickQuestion('帮我写一个关于机房节能降耗的宣传文案。'); showHelpModal = false;"
                        class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/item:opacity-100 hover:scale-105 transition-all flex items-center shadow-sm"
                      >
                        🚀 试一试
                      </button>
                      <div class="transition-all duration-300">
                        <svg v-if="copiedId === 'help_chat'" class="w-3.5 h-3.5 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                        <svg v-else @click="copyToClipboard('帮我写一个关于机房节能降耗的宣传文案。', 'help_chat')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      </div>
                    </div>
                  </div>
               </div>

               <div class="group">
                  <div class="flex items-start space-x-3 mb-2">
                    <div class="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-500">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
                    </div>
                    <div>
                      <div class="text-sm font-bold text-gray-800 dark:text-gray-200">ChatBI 数据分析</div>
                      <p class="text-[11px] text-gray-500 leading-relaxed">支持自然语言查询数据库。您可以问：</p>
                    </div>
                  </div>
                  <div
                    class="ml-9 p-2.5 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-dashed border-gray-200 dark:border-gray-700 relative group/item cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-all"
                  >
                    <p class="text-[10px] text-gray-600 dark:text-gray-400 italic pr-12" @click="copyToClipboard('查询上海区域所有机房的剩余机柜数', 'help_bi')">“查询上海区域所有机房的剩余机柜数”</p>
                    <div class="absolute right-2 top-2.5 flex items-center space-x-2">
                      <button
                        @click="handleQuickQuestion('查询上海区域所有机房的剩余机柜数'); showHelpModal = false;"
                        class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/item:opacity-100 hover:scale-105 transition-all flex items-center shadow-sm"
                      >
                        🚀 试一试
                      </button>
                      <div class="transition-all duration-300">
                        <svg v-if="copiedId === 'help_bi'" class="w-3.5 h-3.5 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                        <svg v-else @click="copyToClipboard('查询上海区域所有机房的剩余机柜数', 'help_bi')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      </div>
                    </div>
                  </div>
               </div>

               <div class="group">
                  <div class="flex items-start space-x-3 mb-2">
                    <div class="p-2 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg text-emerald-500">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>
                    </div>
                    <div>
                      <div class="text-sm font-bold text-gray-800 dark:text-gray-200">知识库问答</div>
                      <p class="text-[11px] text-gray-500 leading-relaxed">基于私有文档提供精准问答。您可以问：</p>
                    </div>
                  </div>
                  <div
                    class="ml-9 p-2.5 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-dashed border-gray-200 dark:border-gray-700 relative group/item cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-all"
                  >
                    <p class="text-[10px] text-gray-600 dark:text-gray-400 italic pr-12" @click="copyToClipboard('本周的 CS 和 ops 工单有哪些？', 'help_kb')">“本周的 CS 和 ops 工单有哪些？”</p>
                    <div class="absolute right-2 top-2.5 flex items-center space-x-2">
                      <button
                        @click="handleQuickQuestion('本周的 CS 和 ops 工单有哪些？'); showHelpModal = false;"
                        class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/item:opacity-100 hover:scale-105 transition-all flex items-center shadow-sm"
                      >
                        🚀 试一试
                      </button>
                      <div class="transition-all duration-300">
                        <svg v-if="copiedId === 'help_kb'" class="w-3.5 h-3.5 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                        <svg v-else @click="copyToClipboard('本周的 CS 和 ops 工单有哪些？', 'help_kb')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      </div>
                    </div>
                  </div>
               </div>

               <div class="group">
                  <div class="flex items-start space-x-3 mb-2">
                    <div class="p-2 bg-amber-50 dark:bg-amber-900/20 rounded-lg text-amber-500">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>
                    </div>
                    <div>
                      <div class="text-sm font-bold text-gray-800 dark:text-gray-200">多步任务执行</div>
                      <p class="text-[11px] text-gray-500 leading-relaxed">支持处理复杂逻辑。您可以问：</p>
                    </div>
                  </div>
                  <div
                    class="ml-9 p-2.5 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-dashed border-gray-200 dark:border-gray-700 relative group/item cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-all"
                  >
                    <p class="text-[10px] text-gray-600 dark:text-gray-400 italic pr-12" @click="copyToClipboard('查询最近 1 小时的网络延迟报警，并写一封邮件告知运维团队。', 'help_task')">“查询最近 1 小时的网络延迟报警，并写一封邮件告知运维团队。”</p>
                    <div class="absolute right-2 top-2.5 flex items-center space-x-2">
                      <button
                        @click="handleQuickQuestion('查询最近 1 小时的网络延迟报警，并写一封邮件告知运维团队。'); showHelpModal = false;"
                        class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/item:opacity-100 hover:scale-105 transition-all flex items-center shadow-sm"
                      >
                        🚀 试一试
                      </button>
                      <div class="transition-all duration-300">
                        <svg v-if="copiedId === 'help_task'" class="w-3.5 h-3.5 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                        <svg v-else @click="copyToClipboard('查询最近 1 小时的网络延迟报警，并写一封邮件告知运维团队。', 'help_task')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      </div>
                    </div>
                  </div>
               </div>
            </div>
          </section>

          <!-- Typical Scenarios -->
          <section>
            <h4 class="text-xs font-black text-primary uppercase tracking-widest mb-4 flex items-center">
               <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
               常见场景 (点击可复制)
            </h4>
            <div class="space-y-2.5">
               <div
                 class="p-3 bg-gradient-to-r from-gray-50 to-white dark:from-gray-900/30 dark:to-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 relative group/scenario cursor-pointer hover:border-primary/50 hover:shadow-md transition-all"
               >
                 <div class="text-[11px] font-bold text-gray-700 dark:text-gray-300 flex items-center mb-1">
                   <span class="w-1 h-1 bg-gray-400 rounded-full mr-2"></span>
                   故障排查
                 </div>
                 <p class="text-[10px] text-gray-500 pr-16" @click="copyToClipboard('查看 B7 机房最近的所有高压报警，并给出可能的根本原因分析。', 'help_scenario_fault')">“查看 B7 机房最近的所有高压报警，并给出可能的根本原因分析。”</p>
                 <div class="absolute right-3 top-1/2 -translate-y-1/2 flex items-center space-x-2">
                    <button
                      @click="handleQuickQuestion('查看 B7 机房最近的所有高压报警，并给出可能的根本原因分析。'); showHelpModal = false;"
                      class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/scenario:opacity-100 hover:scale-105 transition-all shadow-sm"
                    >
                      🚀 试一试
                    </button>
                    <div class="transition-all duration-300">
                      <svg v-if="copiedId === 'help_scenario_fault'" class="w-4 h-4 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                      <svg v-else @click="copyToClipboard('查看 B7 机房最近的所有高压报警，并给出可能的根本原因分析。', 'help_scenario_fault')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    </div>
                 </div>
               </div>
               <div
                 class="p-3 bg-gradient-to-r from-gray-50 to-white dark:from-gray-900/30 dark:to-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 relative group/scenario cursor-pointer hover:border-primary/50 hover:shadow-md transition-all"
               >
                 <div class="text-[11px] font-bold text-gray-700 dark:text-gray-300 flex items-center mb-1">
                   <span class="w-1 h-1 bg-gray-400 rounded-full mr-2"></span>
                   数据巡检
                 </div>
                 <p class="text-[10px] text-gray-500 pr-16" @click="copyToClipboard('统计各机房昨天的监控指标数据量，并进行分析。', 'help_scenario_inspect')">“统计各机房昨天的监控指标数据量，并进行分析。”</p>
                 <div class="absolute right-3 top-1/2 -translate-y-1/2 flex items-center space-x-2">
                    <button
                      @click="handleQuickQuestion('统计各机房昨天的监控指标数据量，并进行分析。'); showHelpModal = false;"
                      class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/scenario:opacity-100 hover:scale-105 transition-all shadow-sm"
                    >
                      🚀 试一试
                    </button>
                    <div class="transition-all duration-300">
                      <svg v-if="copiedId === 'help_scenario_inspect'" class="w-4 h-4 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                      <svg v-else @click="copyToClipboard('统计各机房昨天的监控指标数据量，并进行分析。', 'help_scenario_inspect')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    </div>
                 </div>
               </div>
               <div
                 class="p-3 bg-gradient-to-r from-gray-50 to-white dark:from-gray-900/30 dark:to-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 relative group/scenario cursor-pointer hover:border-primary/50 hover:shadow-md transition-all"
               >
                 <div class="text-[11px] font-bold text-gray-700 dark:text-gray-300 flex items-center mb-1">
                   <span class="w-1 h-1 bg-gray-400 rounded-full mr-2"></span>
                   合规审计
                 </div>
                 <p class="text-[10px] text-gray-500 pr-16" @click="copyToClipboard('查找 2024 年 Q4 季度的所有变更审批记录，汇总为表格。', 'help_scenario_audit')">“查找 2024 年 Q4 季度的所有变更审批记录，汇总为表格。”</p>
                 <div class="absolute right-3 top-1/2 -translate-y-1/2 flex items-center space-x-2">
                    <button
                      @click="handleQuickQuestion('查找 2024 年 Q4 季度的所有变更审批记录，汇总为表格。'); showHelpModal = false;"
                      class="px-1.5 py-0.5 bg-primary text-white text-[9px] rounded opacity-0 group-hover/scenario:opacity-100 hover:scale-105 transition-all shadow-sm"
                    >
                      🚀 试一试
                    </button>
                    <div class="transition-all duration-300">
                      <svg v-if="copiedId === 'help_scenario_audit'" class="w-4 h-4 text-emerald-500 scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                      <svg v-else @click="copyToClipboard('查找 2024 年 Q4 季度的所有变更审批记录，汇总为表格。', 'help_scenario_audit')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    </div>
                 </div>
               </div>
            </div>
          </section>
        </div>

        <!-- Footer -->
        <div class="px-6 py-4 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-end">
          <button
            @click="showHelpModal = false"
            class="px-6 py-2 bg-primary text-white text-xs font-black uppercase tracking-widest rounded-xl hover:opacity-90 transition-all shadow-lg shadow-primary/20"
            :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
          >
            我明白了
          </button>
        </div>
      </div>
    </div>
    <!-- Modal: Add Command -->
    <div
      v-if="showAddModal"
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm p-4"
      @click.self="showAddModal = false"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-sm overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700">
        <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gray-50 dark:bg-gray-800/50">
          <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200">新建快捷指令</h3>
          <button @click="showAddModal = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-[10px] font-bold text-gray-400 uppercase mb-1">显示名称</label>
            <input v-model="newCommand.label" type="text" placeholder="如：🏢 查机房" class="w-full text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-primary transition-all dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-[10px] font-bold text-gray-400 uppercase mb-1">指令内容</label>
            <textarea v-model="newCommand.command" rows="2" placeholder="输入要发送给 AI 的文字..." class="w-full text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-primary transition-all dark:text-gray-100 resize-none"></textarea>
          </div>
          <button @click="addCommand" :disabled="!newCommand.label || !newCommand.command" class="w-full py-2.5 bg-primary text-white text-sm font-bold rounded-lg hover:opacity-90 disabled:opacity-50 transition-all shadow-md shadow-primary/20" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }">
            添加指令
          </button>
        </div>
      </div>
    </div>
    <!-- Modal: Add Command -->
    <div
      v-if="showAddModal"
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm p-4"
      @click.self="showAddModal = false"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-sm overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700">
        <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gray-50 dark:bg-gray-800/50">
          <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200">新建快捷指令</h3>
          <button @click="showAddModal = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="block text-[10px] font-bold text-gray-400 uppercase mb-1">显示名称</label>
            <input v-model="newCommand.label" type="text" placeholder="如：🏢 查机房" class="w-full text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-primary transition-all dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-[10px] font-bold text-gray-400 uppercase mb-1">指令内容</label>
            <textarea v-model="newCommand.command" rows="2" placeholder="输入要发送给 AI 的文字..." class="w-full text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-primary transition-all dark:text-gray-100 resize-none"></textarea>
          </div>
          <button @click="addCommand" :disabled="!newCommand.label || !newCommand.command" class="w-full py-2.5 bg-primary text-white text-sm font-bold rounded-lg hover:opacity-90 disabled:opacity-50 transition-all shadow-md shadow-primary/20" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }">
            添加指令
          </button>
        </div>
      </div>
    </div>

    <ChatModelCallStatsModal
      :visible="showStatsModal"
      :loading="loadingStats"
      :stats="currentStats"
      :expanded="expandedStats"
      @close="showStatsModal = false"
      @toggle="toggleStatExpand"
    />
    <DatasetPortalDrawer
      v-model="showPortalDrawer"
      v-model:keep-open-on-question="portalKeepOpenOnQuestion"
      v-model:pinned="portalPinned"
      v-model:drawer-width="portalDrawerWidthReactive"
      :payload="scopedPortalNavigationPayload"
      :project-resource-scope="projectSessionHasDatasetScope ? '仅显示当前项目会话已挂载的数据集' : ''"
      :mountable-datasets="resourceOptions.datasets"
      :active-metadata-dataset-ids="activeMetadataDatasetIds"
      :session-mounted-dataset-ids="sessionMountedMetadataDatasetIds"
      :initial-loading="portalLoading && !portalNavigationPayload"
      :background-refreshing="portalBackgroundRefreshing"
      :focus-saved-report-request="savedReportFocusRequest"
      @quick-question="handlePortalQuickQuestion"
      @record-question-click="(payload) => recordDatasetMenuQuestionClick(scopedPortalNavigationPayload, payload)"
      @clear-question-click="(payload) => clearDatasetMenuQuestionClick(scopedPortalNavigationPayload, payload)"
      @toggle-metadata-dataset="(datasetId) => toggleMetadataDatasetActive(datasetId, chatInputRef, resourceOptions.datasets)"
      @pin-metadata-dataset="pinMetadataDatasetToSession"
      @unpin-metadata-dataset="unpinMetadataDatasetFromSession"
      @refresh="refreshPortalNavigation"
      @execute-saved-report="handleExecuteSavedReport"
      @edit-saved-report="openEditReportModal"
      @open-full-page="openFullDataPortal"
    />

    <!-- TraceLogViewer -->
    <TraceLogViewer
      :trace-id="embedTraceId"
      :visible="showEmbedTrace"
      @close="showEmbedTrace = false"
    />
    <ChatBIMonitorDialog
      :open="chatbiMonitorDialogOpen"
      :conversation-id="conversationId"
      :result-id="chatbiMonitorResultId"
      @close="chatbiMonitorDialogOpen = false"
      @created="handleChatBIMonitorCreated"
    />
    <BrowserPanel
      :visible="browserPanelVisible"
      :loading="browserPanelOpening || (browserPanelVisible && !browserSessionId)"
      :refresh-signal="browserRefreshSignal"
      :session-id="browserSessionId"
      :viewer-token="browserViewerToken"
      :environment-error="browserEnvironmentError"
      :auth-token="config.token"
      :approval-mode="browserApprovalMode"
      v-model:pinned="browserPinned"
      v-model:panel-width="browserPanelWidthReactive"
      @close="closeBrowserPanel"
      @close-session="closeBrowserSession"
      @retry="openBrowserPanel"
      @update:approval-mode="updateBrowserApprovalMode"
      @ask-ai-crop="handleBrowserCropAskAi"
    />
    <WebPreviewPanel
      :visible="webPreviewVisible"
      :url="webPreviewUrl"
      :pinned-dock-right="webPreviewDockRightPx"
      v-model:pinned="webPreviewPinned"
      v-model:panel-width="webPreviewPanelWidthReactive"
      @close="closeWebPreviewPanel"
    />
    </div>
</template>
<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, nextTick, watch, computed, triggerRef } from "vue";
import { CommandLineIcon } from "@heroicons/vue/24/outline";
import { useRouter } from "vue-router";
import axios from "@/utils/axios";
import { finalizeConversation } from "@/utils/conversationFinalize";
import { cancelConversationRun } from "@/utils/cancelConversationRun";
import { createConversationId } from "@/utils/conversationId";
import { useToast } from "../composables/useToast";
import { useTokenQuota } from "../composables/useTokenQuota";
import { useContextUsage } from "@/composables/useContextUsage";
import { useContextCompactions } from "@/composables/useContextCompactions";
import { buildQuotaStatusMarkdown } from "@/utils/quotaDisplay";
import { useDatasetPortal } from "@/composables/useDatasetPortal";
import { useDatasetMount } from "@/composables/useDatasetMount";
import {
  DATASET_PORTAL_SLASH_COMMAND,
  DATASET_PORTAL_SYSTEM_COMMAND_ID,
  isDatasetPortalSlashCommand,
  KNOWLEDGE_PORTAL_SLASH_COMMAND,
  KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID,
  isKnowledgePortalSlashCommand,
} from "@/constants/datasetPortalCommand";
import {
  WORKSPACE_SLASH_COMMAND,
  WORKSPACE_SYSTEM_COMMAND_ID,
  isWorkspaceSlashCommand,
} from "@/constants/workspaceCommand";
import {
  MY_ARTIFACTS_SLASH_COMMAND,
  MY_ARTIFACTS_SYSTEM_COMMAND_ID,
  isMyArtifactsSlashCommand,
} from "@/constants/artifactsCommand";

import { useBranding } from "@/composables/useBranding";
import agentAvatarUrl from "@/assets/nanzi-agent-avatar.svg";

const toast = useToast();
const router = useRouter();
const openFullDataPortal = () => {
  if (window.parent !== window) {
    postMessageToHost({ type: "OPEN_DATA_PORTAL_FULL" });
    return;
  }
  router.push({ path: "/dashboard/personal", query: { tab: "data" } });
};
const { branding } = useBranding();
const {
  bannerMessage: quotaBannerMessage,
  isBlocked: quotaIsBlocked,
  quotaStatus,
  refreshQuota,
  ensureCanSend,
} = useTokenQuota();
const showToast = toast.showToast;
import MessageRenderer from "@/components/MessageRenderer.vue";
import ToolPermissionCard from "@/components/chat/ToolPermissionCard.vue";
import GroundingBlockedCard from "@/components/GroundingBlockedCard.vue";
import BusinessConfirmationCard from "@/components/BusinessConfirmationCard.vue";
import UserQuestionCard from "@/components/UserQuestionCard.vue";
import DatasetCapabilityMenu from "@/components/chatbi/DatasetCapabilityMenu.vue";
import DatasetPortalDrawer from "@/components/chatbi/DatasetPortalDrawer.vue";
import ChatBIInsightPanel from "@/components/chatbi/ChatBIInsightPanel.vue";
import ChatBIContinueAnalysis from "@/components/chatbi/ChatBIContinueAnalysis.vue";
import MessageContinueAnalysis from "@/components/chat/MessageContinueAnalysis.vue";
import MessageActionMenus from "@/components/chat/MessageActionMenus.vue";
import ReusableResultNotice from "@/components/chat/ReusableResultNotice.vue";
import ErrorDetailCard from "@/components/chat/ErrorDetailCard.vue";
import ChatBIMonitorDialog from "@/components/chatbi/ChatBIMonitorDialog.vue";
import BrowserPanel from "@/components/embed/BrowserPanel.vue";
import WebPreviewPanel from "@/components/embed/WebPreviewPanel.vue";
import { isBrowserOpenableUrl } from "@/utils/messageBrowserLinks";
import ChatBIMetadataGuide from "@/components/chatbi/ChatBIMetadataGuide.vue";
import AgentHandoffNotice from "@/components/chat/AgentHandoffNotice.vue";
import type { ChatBIInsightMeta } from "@/types/chatbiInsight";
import { applyChatBIInsightEvent } from "@/utils/chatbiInsight";
import type { ChatBIMetadataGuide as ChatBIMetadataGuidePayload } from "@/types/chatbiMetadataGuide";
import { applyChatBIMetadataGuideEvent } from "@/utils/chatbiMetadataGuide";
import type { AgentHandoffNoticeData } from "@/types/agentHandoff";
import { applyAgentHandoffEvent } from "@/utils/agentHandoff";
import KnowledgePortalDrawer from "@/components/knowledge/KnowledgePortalDrawer.vue";
import { useKnowledgePortal } from "@/composables/useKnowledgePortal";
import CitationPopover from "@/components/CitationPopover.vue";
import { copyToClipboard as copyTextSecure } from "@/utils/clipboard";
import {
  applyStreamErrorMessage,
  type StreamErrorDetail,
} from "@/utils/streamErrorPresentation";
import RagPreviewDrawer from "@/components/RagPreviewDrawer.vue";
import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ChatSettings from "@/components/embed/ChatSettings.vue";
import ChatCanvas from "@/components/embed/ChatCanvas.vue";
import ChatExecutionTimeline from "@/components/chat/ChatExecutionTimeline.vue";
import ChatTodoCard from "@/components/chat/ChatTodoCard.vue";
import BashEnvBanner from "@/components/chat/BashEnvBanner.vue";
import DockerWorkspaceBanner from "@/components/chat/DockerWorkspaceBanner.vue";
import DockerTerminalModal from "@/components/chat/DockerTerminalModal.vue";
import ChatInput from "@/components/embed/ChatInput.vue";
import WelcomeDashboard from "@/components/embed/WelcomeDashboard.vue";
import PersonalResourcesModal from "@/components/embed/PersonalResourcesModal.vue";
import PortalNotificationBell from "@/components/PortalNotificationBell.vue";
import WorkspaceBrowserDrawer from "@/components/embed/WorkspaceBrowserDrawer.vue";
import MyArtifactsDrawer from "@/components/embed/MyArtifactsDrawer.vue";
import MemoryBrowserDrawer from "@/components/embed/MemoryBrowserDrawer.vue";
import { useWorkbenchHome } from "@/composables/useWorkbenchHome";
import { resolveGeneratedFileHref } from "@/utils/generatedFileUrl";
import { artifactApi } from "@/api/artifact";
import {
  personalResourceFallbackItems,
  personalResourcePlaceholderItems,
  filterEmbedWelcomePersonalResources,
  isInboxPersonalResource,
  type PersonalResourceTab,
} from "@/constants/personalResources";
import type { SavedReportOpenRequest } from "@/utils/savedReportOpenProtocol";
import SkillCreatedBanner from "@/components/chat/SkillCreatedBanner.vue";
import { parseSkillCreatedMarker, type SkillCreatedInfo } from "@/utils/skillCreated";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import SessionResourceScopeBar from "@/components/embed/SessionResourceScopeBar.vue";
import ResourceScopeModal from "@/components/embed/ResourceScopeModal.vue";
import { isImageAttachment } from "@/utils/attachmentImages";
import { isDirectRenderableUrl, resolvePublicUploadsPreviewUrl } from "@/utils/workspaceFilePreview";
import TraceLogViewer from "@/components/TraceLogViewer.vue";
import ChatModelCallStatsModal from "@/components/chat/ChatModelCallStatsModal.vue";
import DataPortalReportCreateModal from "@/components/data-portal/DataPortalReportCreateModal.vue";
import SavedReportRunModal from "@/components/chat/SavedReportRunModal.vue";
import {
  sanitizeStreamContent,
  stripInternalContextBlocks,
} from "@/utils/streamContentSanitize";
import { normalizeAgentSwitchCommand } from "@/utils/agentSwitchCommands";
import { createSseLineParser } from "@/utils/chartRenderer";
import { modelApi, type AIModel, type ReasoningEffort } from "@/api/model";
import {
  type TurnType,
} from "@/utils/turnLogDisplay";
import {
  buildSkillFlowBadges,
  summarizeSkillFlowBadges,
  type SkillFlowBadge,
} from "@/utils/skillFlowBadges";
import {
  canSaveGoldenReportFromMessage,
  resolveSavableSqlFromMessage,
} from "@/utils/toolLogDisplay";
import {
  deriveSavedReportDescription,
  deriveSavedReportTagsInput,
  deriveSavedReportTitle,
  parseRequirementAnalysisFromMessage,
  resolveSavedReportSourceContext,
} from "@/utils/savedReportDefaults";
import {
  buildSavedReportRunParams,
  composeSavedReportExecuteMarkdown,
  detectSavedReportDateTemplate,
  extractColumnMetaFromAgentMessage,
  extractSavedReportExecuteErrorMessage,
  mergeSavedReportAnalysisIntoResult,
  todayDateString,
  todayMonthString,
} from "@/composables/chat/useSavedReportWorkflow";
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas";
import { createChatSendGate } from "@/composables/chat/useChatSendGate";
import { useConversationRunStatus } from "@/composables/chat/useConversationRunStatus";
import { createClientRequestId } from "@/utils/clientRequestId";
import {
  USER_MESSAGE_CONTEXT_DIVIDER,
  splitUserMessageContent,
  useChatAttachments,
} from "@/composables/chat/useChatAttachments";
import { groupChatHistoryByDate } from "@/composables/chat/useChatHistoryGroups";
import {
  applyStreamTraceId,
  appendAssistantBodyDelta,
  dispatchAgentscopeStreamEvent,
  formatExternalExecutionStatus,
  resolveStreamLogDurationMs,
  finalizeAllPendingStreamLogs,
  markStalePendingStreamLogs,
  mergeStreamCitations,
  resumeExternalExecutionStream,
  syncProcessTimelineLog,
  type PendingExternalExecution,
  type PendingToolPermission,
  type GroundingBlockedAction,
  type GroundingBlockedPayload,
} from "@/utils/agentscopeSseHandlers";
import { hydrateHistoryProcessTimeline } from "@/utils/processTimeline";
import { normalizeSubagentTraceMeta, type SubagentTraceMeta } from "@/utils/subagentTrace";
import {
  buildBusinessConfirmationUserMessage,
  type BusinessConfirmationField,
  type BusinessConfirmationState,
} from "@/utils/businessConfirmation";
import {
  buildUserQuestionUserMessage,
  type UserQuestionState,
} from "@/utils/userQuestion";
// --- Types ---
interface LogEntry {
  id: number | string;
  parent_id?: string | number;
  name?: string;
  title: string;
  details: string;
  status: "pending" | "success" | "error";
  error_reason?: string;
  isExpanded: boolean;
  isRouter?: boolean;
  category?: 'router' | 'sql' | 'knowledge' | 'tool' | 'tool_resolution' | 'intent' | 'permission' | 'external' | 'model' | 'agent' | 'context' | 'business_confirmation' | 'user_question' | 'system' | 'default';
  tool_name?: string;
  file_metadata?: import("@/utils/processTimeline").FileToolMetadata;
  resolution_status?: 'disabled' | 'missing' | 'filtered';
  execution_time_ms?: number | null;
  elapsed_time_ms?: number | null;
  started_at?: number | null;
  subagent?: SubagentTraceMeta;
  rowFilterApplied?: boolean;
}

interface SavedReportPayload {
  id: string;
  title: string;
  sql_content: string;
  mode?: string;
  sql_template?: string;
  params_schema?: Array<{ type?: string; name?: string; label?: string; default?: any; required?: boolean; options?: any[] }>;
  default_params?: Record<string, any>;
  analysis_mode?: string;
  description?: string;
  tags?: string[];
}

interface PermissionNotice {
  row_filter_applied?: boolean;
  dataset_name?: string;
  rule_count?: number;
  message?: string;
}

interface SkillMeta {
  id?: string;
  name: string;
  description?: string;
}
interface ChatFile {
  type?: string;
  url: string;
  filename: string;
  size: number;
  ext: string;
  skillMeta?: SkillMeta;
  memoryMeta?: any[];
}

interface ChatSendSnapshot {
  content: string;
  files: ChatFile[];
  clientRequestId: string;
  groundingAction?: Record<string, unknown>;
  reusableResultId?: string | null;
  quickContext?: QuickQuestionContext;
}

interface ChatSendOverrides {
  content?: string;
  files?: ChatFile[];
  clientRequestId?: string;
  groundingAction?: Record<string, unknown>;
  reusableResultId?: string | null;
  quickContext?: QuickQuestionContext;
}

interface QuickQuestionContext {
  source: "chatbi_result";
  result_id?: string;
  requires_fresh_data: true;
}

interface QuickQuestionPayload {
  question: string;
  quick_context?: QuickQuestionContext;
}
interface DatasetCapabilityQuestion {
  label: string;
  query: string;
  type?: string;
  click_count?: number;
  last_clicked_at?: string;
}
interface DatasetNavigationPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: Array<{
    id?: string;
    title: string;
    summary: string;
    tags?: string[];
    questions?: DatasetCapabilityQuestion[];
    related_data?: Array<{
      dataset?: string;
      display_name?: string;
      tables?: string[];
      table_descriptions?: Array<{ name: string; description?: string }>;
      table_physical_names?: Record<string, string>;
    }>;
    followups?: DatasetCapabilityQuestion[];
    updated_at?: string;
    enabled?: boolean;
  }>;
  markdown?: string;
  is_fallback?: boolean;
  has_datasets?: boolean;
  from_cache?: boolean;
  llm_generation_failed?: boolean;
  llm_error_message?: string | null;
  refresh_disabled_reason?: string | null;
  _failed_at?: string;
}
interface Message {
  id: number;
  trace_id?: string;
  role: "user" | "agent" | "system";
  content: string;
  errorDetail?: StreamErrorDetail;
  reasoningContent?: string;
  isReasoningExpanded?: boolean;
  files?: ChatFile[];
  logs?: LogEntry[];
  citations?: any[];
  isThinking?: boolean;
  isThoughtExpanded?: boolean;
  processNarration?: string;
  processNarrationPending?: string;
  processTimeline?: import("@/utils/processTimeline").ProcessTimelineItem[];
  isProcessNarrationExpanded?: boolean;
  isCitationsExpanded?: boolean;
  thoughtStartTime?: number;
  thoughtDuration?: string;
  thinkingText?: string;
  agentName?: string;
  agentDisplayName?: string;
  agentType?: string;
  isSavedReportResult?: boolean;
  turnType?: TurnType | string;
  hasDataOutput?: boolean;
  chatbiInsight?: ChatBIInsightMeta;
  chatbiMetadataGuide?: ChatBIMetadataGuidePayload;
  agentHandoff?: AgentHandoffNoticeData;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  feedback?: "up" | "down" | null;
  timestamp?: string;
  isTimeLabel?: boolean;
  pendingPermission?: PendingToolPermission;
  pendingExternalExecution?: PendingExternalExecution;
  toolResultData?: Record<string, Array<{ block_id?: string; media_type?: string; data?: unknown; url?: string | null }>>;
  datasetNavigation?: DatasetNavigationPayload;
  permissionNotice?: PermissionNotice;
  groundingBlocked?: GroundingBlockedPayload;
  reusableResultStatus?: {
    status: "saved" | "reused" | "fallback" | string;
    resultId?: string | null;
    originName?: string | null;
  };
  businessConfirmation?: BusinessConfirmationState;
  userQuestion?: UserQuestionState;
  _hasSilentlyRefreshed?: boolean;
}

const isAgentTimelineMessage = (msg: Message): boolean => {
  const role = (msg as unknown as { role?: string }).role;
  return role === "agent" || role === "assistant";
};

const reusableResultCountByTrace = ref<Record<string, number>>({});
const currentMessageReusableCount = (msg: Message): number => {
  const status = msg.reusableResultStatus?.status;
  const traceResultCount = msg.trace_id ? reusableResultCountByTrace.value[msg.trace_id] || 0 : 0;
  return Math.max(traceResultCount, msg.hasDataOutput || (
    Boolean(msg.reusableResultStatus?.resultId)
    && (status === "saved" || status === "reused")
  ) ? 1 : 0);
};

// Helper: Check Role
const checkRole = (msg: Message, role: string): boolean => {
  return msg.role === role;
};
// Helper: Format Timestamp for Separators
const formatTimeLabel = (isoStr: string): string => {
  try {
    const date = new Date(isoStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const oneDay = 24 * 60 * 60 * 1000;
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    if (diff < oneDay && date.getDate() === now.getDate()) {
      return `${hours}:${minutes}`;
    }
    const yesterday = new Date(now.getTime() - oneDay);
    if (date.getDate() === yesterday.getDate() && date.getMonth() === yesterday.getMonth()) {
       return `昨天 ${hours}:${minutes}`;
    }
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${month}-${day} ${hours}:${minutes}`;
  } catch (e) { return ""; }
};

function visibleStreamBody(msg: Message): string {
  return msg.role === "agent"
    ? stripInternalContextBlocks(msg.content || "")
    : (msg.content || "");
}

function getSkillFlowBadgesForMessage(msg: Message, allMessages: Message[]): SkillFlowBadge[] {
  if (msg.role !== 'agent') return [];
  const idx = allMessages.findIndex(m => m.id === msg.id);
  if (idx <= 0) return [];
  let files: ChatFile[] = [];
  for (let i = idx - 1; i >= 0; i--) {
    const prev = allMessages[i];
    if (!prev) continue;
    if (prev.role === 'user') {
      files = prev.files || [];
      break;
    }
  }
  return buildSkillFlowBadges(files, msg.logs || []);
}

// Helper: Format Timestamp for Bubbles (Smart Date)
const formatBubbleTime = (isoStr: string): string => {
  if (!isoStr) return "";
  try {
    const date = new Date(isoStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const oneDay = 24 * 60 * 60 * 1000;
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    // Today
    if (diff < oneDay && date.getDate() === now.getDate()) {
      return `${hours}:${minutes}`;
    }
    // Yesterday
    const yesterday = new Date(now.getTime() - oneDay);
    if (date.getDate() === yesterday.getDate() && date.getMonth() === yesterday.getMonth()) {
       return `昨天 ${hours}:${minutes}`;
    }
    // Older
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${month}-${day} ${hours}:${minutes}`;
  } catch(e) { return ""; }
};
// --- State ---
const messages = ref<Message[]>([]);
const lastAgentMessage = computed(() => {
    return [...messages.value].reverse().find(m => m.role === 'agent' && !m.isTimeLabel);
});
const displayMessages = computed(() => {
  const raw = messages.value;
  if (!raw || raw.length === 0) return [];
  const result: Message[] = [];
  let lastTime = 0;
  const tryAddDateLabel = (currMsg: Message) => {
    if (currMsg.timestamp) {
      const currTime = new Date(currMsg.timestamp).getTime();
      // 5 minutes threshold
      if (currTime - lastTime > 300000) {
         result.push({
           id: -currTime,
           role: 'system',
           content: formatTimeLabel(currMsg.timestamp),
           isTimeLabel: true
         });
         lastTime = currTime;
      }
    }
  };
  if (raw[0]) {
    tryAddDateLabel(raw[0]);
    result.push(raw[0]);
    if (raw[0].timestamp) lastTime = new Date(raw[0].timestamp).getTime();
  }
  for (let i = 1; i < raw.length; i++) {
    const prev = raw[i - 1];
    const curr = raw[i];
    if (prev && curr && curr.role === prev.role && curr.content === prev.content && !curr.isThinking) {
      continue;
    }
    if (curr) {
       tryAddDateLabel(curr);
       result.push(curr);
       if (curr.timestamp) lastTime = new Date(curr.timestamp).getTime();
    }
  }
  return result;
});
const currentExpertAgent = computed(() => {
  if (config.routingMode === 'expert' && config.expertAgentId) {
    return (
      allowedAgents.value.find(a => a.id === config.expertAgentId)
      || (pinnedAgentId.value === config.expertAgentId ? pinnedAgent.value : null)
      || null
    );
  }
  return null;
});
/** 与发消息时 agent_id 一致；用于判断是否托管引擎（RAGFlow / OpenClaw）以隐藏点赞踩 */
const effectiveEmbedChatAgentId = computed(() => {
  if (config.routingMode === "expert" && config.expertAgentId) {
    return config.expertAgentId;
  }
  return config.overrideAgentId || config.agentId || "";
});
const hideEmbedLikeDislike = computed(() => {
  const id = effectiveEmbedChatAgentId.value;
  if (!id) return false;
  const ag = allowedAgents.value.find((a) => a.id === id);
  const t = ag?.engine_type;
  return t === "RAGFLOW" || t === "OPENCLAW";
});
const chatInputRef = ref<any>(null);
const userInput = ref("");
const showWorkspaceDrawer = ref(false);
const showMyArtifactsDrawer = ref(false);
const myArtifactsInitialTab = ref<"files" | "reusable">("files");
const selectedReusableResultId = ref<string | null>(null);
const focusedReusableResultId = ref<string | null>(null);
const focusedOutputTraceId = ref<string | null>(null);
const reusedReusableResultId = ref<string | null>(null);
const workspaceDrawerRef = ref<{ refreshDirectory: (path?: string) => Promise<void> } | null>(null);

const readStoredBoolean = (key: string, defaultWhenUnset: boolean) => {
  const stored = localStorage.getItem(key);
  if (stored === "1") return true;
  if (stored === "0") return false;
  return defaultWhenUnset;
};

const workspaceKeepOpenOnSelect = ref(
  readStoredBoolean(
    "embed_workspace_keep_open",
    typeof window !== "undefined" &&
      !window.matchMedia("(max-width: 639px)").matches,
  ),
);
watch(workspaceKeepOpenOnSelect, (val) => {
  localStorage.setItem("embed_workspace_keep_open", val ? "1" : "0");
});

const workspacePinned = ref(
  typeof window !== "undefined" &&
    !window.matchMedia("(max-width: 639px)").matches &&
    readStoredBoolean("embed_workspace_pinned", true),
);
watch(workspacePinned, (val) => {
  // 窄屏下抽屉会强制取消钉住，属于临时状态，不覆盖桌面端的钉住偏好
  if (window.matchMedia("(max-width: 639px)").matches) return;
  localStorage.setItem("embed_workspace_pinned", val ? "1" : "0");
});

/** 桌面端打开工作空间时强制钉住；用户仍可手动取消钉住 */
const openWorkspaceDrawer = () => {
  if (!window.matchMedia("(max-width: 639px)").matches) {
    workspacePinned.value = true;
  }
  showWorkspaceDrawer.value = true;
};
const toggleWorkspaceDrawer = () => {
  if (showWorkspaceDrawer.value) {
    showWorkspaceDrawer.value = false;
    return;
  }
  openWorkspaceDrawer();
};

const toggleMyArtifactsDrawer = () => {
  if (!showMyArtifactsDrawer.value) {
    myArtifactsInitialTab.value = "files";
    focusedReusableResultId.value = null;
    focusedOutputTraceId.value = null;
  }
  showMyArtifactsDrawer.value = !showMyArtifactsDrawer.value;
};

const openReusableResults = (resultId?: string | null, traceId?: string | null, status?: string | null) => {
  myArtifactsInitialTab.value = "reusable";
  focusedReusableResultId.value = resultId || null;
  focusedOutputTraceId.value = traceId || null;
  reusedReusableResultId.value = status === "reused" && resultId ? resultId : null;
  showMyArtifactsDrawer.value = true;
};

const openMessageArtifacts = (traceId?: string | null) => {
  myArtifactsInitialTab.value = "files";
  focusedReusableResultId.value = null;
  focusedOutputTraceId.value = traceId || null;
  reusedReusableResultId.value = null;
  showMyArtifactsDrawer.value = true;
};

const selectReusableResult = (result: { result_id: string; origin_name?: string }) => {
  selectedReusableResultId.value = result.result_id;
  focusedReusableResultId.value = result.result_id;
  showMyArtifactsDrawer.value = false;
  showToast(
    `已选择${result.origin_name ? `「${result.origin_name}」` : "该结果"}，下一轮将优先复用`,
    "success",
  );
};

const showMemoryDrawer = ref(false);

const memoryKeepOpenOnSelect = ref(
  readStoredBoolean(
    "embed_memory_keep_open",
    typeof window !== "undefined" &&
      !window.matchMedia("(max-width: 639px)").matches,
  ),
);
watch(memoryKeepOpenOnSelect, (val) => {
  localStorage.setItem("embed_memory_keep_open", val ? "1" : "0");
});

const memoryPinned = ref(
  typeof window !== "undefined" &&
    !window.matchMedia("(max-width: 639px)").matches &&
    readStoredBoolean("embed_memory_pinned", false),
);
watch(memoryPinned, (val) => {
  localStorage.setItem("embed_memory_pinned", val ? "1" : "0");
});

const attachedMemoryConversationIds = computed(() => {
  const memFile = chatInputRef.value?.uploadedFiles?.find((f: any) => f.type === "memory");
  return memFile?.url ? String(memFile.url) : "";
});

const showStatsModal = ref(false);
const loadingStats = ref(false);
const currentStats = ref<any[]>([]);
const expandedStats = ref<Record<string, boolean>>({});

const toggleStatExpand = (callIndex: number) => {
  expandedStats.value[callIndex] = !expandedStats.value[callIndex];
};

watch(showStatsModal, (newVal) => {
  if (!newVal) {
    expandedStats.value = {};
  }
});

/** 知识库问答专家候选（capability 或名称命中） */
const listKnowledgeExpertAgents = () => {
  return allowedAgents.value.filter((a: any) => {
    const capabilities = Array.isArray(a?.capabilities) ? a.capabilities : [];
    if (capabilities.includes("knowledge_base")) return true;
    const name = String(a?.name || "").toLowerCase();
    const label = String(a?.display_name || "");
    return (
      name === "knowledge-base" ||
      name.includes("knowledge") ||
      label.includes("知识库")
    );
  });
};

/** 仅当恰好 1 个知识库智能体时返回，多个则不自动锁定 */
const resolveKnowledgeExpertAgent = () => {
  const matches = listKnowledgeExpertAgents();
  return matches.length === 1 ? matches[0] : undefined;
};

const buildKnowledgeBaseAttachmentHint = (datasetIdLine: string) => {
  const expert = resolveKnowledgeExpertAgent();
  const expertHint = expert
    ? `本次为知识库查询，须优先由知识库专家「${expert.display_name || expert.name}」处理（agent_name: ${expert.name}，agent_id: ${expert.id}）；自动路由时必须选择该专家，不得分发给 ChatBI、运维或其他专家。`
    : `本次为知识库查询，须优先选择知识库专家（agent_name: knowledge-base）；自动路由时不得分发给 ChatBI、运维或其他专家。`;

  return `${expertHint}\n\n【必须执行】${datasetIdLine}`;
};

const buildDatasetAttachmentHint = (datasetIdLine: string) => {
  const expert = findUniqueDataQueryAgent();
  const expertHint = expert
    ? `本次为数据查询与分析，须优先由数据查询专家「${expert.display_name || expert.name}」（agent_name: ${expert.name}，agent_id: ${expert.id}）处理；自动路由时必须选择该专家，不得分发给主助手或其他专家。`
    : `本次为数据查询与分析，须优先选择具有数据查询能力的专家智能体处理；自动路由时不得分发给其他无关专家。`;

  return `${expertHint}\n\n【必须执行】${datasetIdLine}`;
};

const { appendAttachmentContext } = useChatAttachments({
  buildKnowledgeBaseAttachmentHint,
  buildDatasetAttachmentHint,
});

const handleSelectLocalFs = (payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const exists = files.some((f: any) => f.type === payload.type && f.url === payload.path);
  if (!exists) {
    chatInputRef.value.uploadedFiles.push({
      type: payload.type,
      url: payload.path,
      filename: payload.name,
      size: payload.size,
      ext: payload.ext
    });
  }
};

const handleWorkspaceContentSaved = (payload: { path: string }) => {
  if (showWorkspaceDrawer.value) {
    void workspaceDrawerRef.value?.refreshDirectory(payload.path);
  }
};

const isImageFile = isImageAttachment;

const formatBytes = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const resolveReqContent = (msg: Message) => {
  let reqContent = msg.content || "";
  if (msg.role === "user" && msg.files && msg.files.length > 0) {
    if (!reqContent.includes(USER_MESSAGE_CONTEXT_DIVIDER)) {
      reqContent = appendAttachmentContext(msg.content, msg.files);
    }
  }
  return reqContent;
};

/** 收集会话内知识库 dataset ID（输入框附件 + 历史 user 消息附件，追问时继承首轮选择） */
const collectKnowledgeDatasetIds = (): string[] => {
  const ids: string[] = [];
  const pushId = (raw: string) => {
    const value = String(raw || "").trim();
    if (value && !ids.includes(value)) ids.push(value);
  };
  const uploaded = chatInputRef.value?.uploadedFiles || [];
  uploaded.forEach((file: any) => {
    if (file.type === "knowledge_base") pushId(file.url);
  });
  const sendable = messages.value.filter((m) => !m.isThinking && (m.content || m.files));
  sendable.forEach((m) => {
    if (m.role !== "user") return;
    m.files?.forEach((file: any) => {
      if (file.type === "knowledge_base") pushId(file.url);
    });
  });
  return ids;
};

const isChatContextMessage = (message: Message): boolean => (
  !message.isThinking &&
  (message.role === "user" || message.role === "agent") &&
  Boolean(message.content || message.files?.length)
);

/** 发给 API 的消息：已有会话只发送当前轮 user，历史由服务端 Redis 提供。 */
const buildOutboundMessages = () => {
  const sendable = messages.value.filter(isChatContextMessage);
  if (conversationId.value) {
    const latestUser = [...sendable].reverse().find((m) => m.role === "user");
    if (latestUser) {
      const msgObj: any = {
        role: "user",
        content: resolveReqContent(latestUser),
      };
      if (latestUser.files?.length) {
        msgObj.files = latestUser.files;
      }
      return [msgObj];
    }
    return [];
  }

  const lastUserIdx = sendable.reduce(
    (last, m, i) => (m.role === "user" ? i : last),
    -1,
  );

  return sendable.map((m, idx) => {
    const role = m.role === "agent" ? "assistant" : m.role;
    if (m.role === "user" && idx !== lastUserIdx) {
      return {
        role,
        content: splitUserMessageContent(m.content || "").userPart,
      };
    }
    const msgObj: any = {
      role,
      content: m.role === "user"
        ? resolveReqContent(m)
        : stripInternalContextBlocks(m.content || ""),
    };
    if (m.role === "user" && m.files?.length) {
      msgObj.files = m.files;
    }
    return msgObj;
  });
};

const embedTraceId = ref("");
const showEmbedTrace = ref(false);
const openEmbedTrace = (traceId: string) => {
  embedTraceId.value = traceId;
  showEmbedTrace.value = true;
};
const isProcessing = ref(false);
const { locked: sendLocked, runExclusive: runSendExclusive } = createChatSendGate();
const bashBannerEnv = ref<"host" | "docker" | "e2b" | "ssh" | null>(null);
const bashBannerDismissed = ref(false);
const showBashBanner = computed(
  () => bashBannerEnv.value !== null && !bashBannerDismissed.value && config.showBashBanner
);
const handleBashEnvEvent = (env: "host" | "docker" | "e2b" | "ssh") => {
  bashBannerEnv.value = env;
  bashBannerDismissed.value = false;
};
/** 统一开关 Bash 横幅提示：写入 config 并持久化到 localStorage（1=关，0=开） */
const setBashBannerVisible = (visible: boolean) => {
  config.showBashBanner = visible;
  localStorage.setItem("bash_env_banner_ignored", visible ? "0" : "1");
  bashBannerEnv.value = null;
  bashBannerDismissed.value = false;
  showToast(
    visible ? "Bash 运行环境横幅提示已开启" : "Bash 运行环境横幅提示已关闭",
    visible ? "success" : "info",
  );
};
const handleIgnoreBashBanner = () => {
  setBashBannerVisible(false);
};
const activeTodoTimeline = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i];
    if (!msg) continue;
    if (isAgentTimelineMessage(msg)) {
      const hasTodo = msg.processTimeline?.some((item) => item.kind === 'todo');
      if (hasTodo) {
        return msg.processTimeline;
      }
    }
  }
  return undefined;
});
const datasetMenuLoading = ref(false);
const isInitialLoading = ref(true);
const messagesContainer = ref<HTMLDivElement | null>(null);
// Scroll State
const isAtBottom = ref(true);
const showNewMessageHint = ref(false);
const autoScrollEnabled = ref(true);
/** 程序滚底后的短时间内忽略 handleScroll 的「误判为手动上滚」，避免关掉 autoScroll（smooth 中间帧会触发） */
const programmaticScrollUntil = ref(0);
// Config
const config = reactive({
  token: "",
  agentId: "",
  instanceId: "", // For isolation
  theme: "light",
  welcomeMessage: "",
  overrideModel: "", // To override default model
  approvalMode: "ask" as "ask" | "allow" | "deny",
  overrideAgentId: "", // To override agent via @mention
  userAvatar: "", // Custom user avatar URL
  routingMode: "auto", // 'auto' | 'expert'
  expertAgentId: "",
  enableMultiAgent: true,
  showShortcuts: true,
  enableSqlPlan: false,
  enableGrounding: true, // Embed 默认开启反幻觉校验
  groundingBlockMode: "strict_buffer" as "strict_buffer" | "stream_with_retraction",
  expandThoughts: true, // 思考过程默认展示开关
  markdownTheme: "default" as "default" | "minimal" | "academic" | "apple" | "warm" | "compact",
  hideMessageBorder: true,
  /** Bash 运行环境横幅提示开关（可在设置面板中切换，localStorage 持久化） */
  showBashBanner: localStorage.getItem("bash_env_banner_ignored") !== "1",
});
type BrowserApprovalMode = "guarded" | "autopilot";
const browserPanelVisible = ref(false);
const browserSessionId = ref<string | null>(null);
const browserViewerToken = ref<string | null>(null);
const browserApprovalMode = ref<BrowserApprovalMode>("autopilot");
const browserPinned = ref(true);
const browserPanelOpening = ref(false);
const browserEnvironmentError = ref<string | null>(null);
const browserRefreshSignal = ref(0);
const webPreviewVisible = ref(false);
const webPreviewUrl = ref<string | null>(null);
let browserOpenGeneration = 0;

const attachBrowserSession = async (
  sessionId: string,
  approvalMode?: string,
  openingGeneration?: number,
): Promise<boolean> => {
  if (!sessionId) return false;
  if (!config.token) {
    showToast("浏览器需要有效的登录凭证", "warning");
    return false;
  }
  try {
    const tokenResponse = await axios.post(
      `/api/v1/chat/browser/sessions/${encodeURIComponent(sessionId)}/viewer-token`,
      {},
      { headers: embedAuthHeaders() },
    );
    if (openingGeneration !== undefined && openingGeneration !== browserOpenGeneration) return false;
    browserSessionId.value = sessionId;
    browserViewerToken.value = tokenResponse.data.token;
    browserApprovalMode.value = approvalMode === "autopilot" ? "autopilot" : "guarded";
    browserPinned.value = true;
    browserPanelVisible.value = true;
    return true;
  } catch (error: any) {
    if (openingGeneration !== undefined && openingGeneration !== browserOpenGeneration) return false;
    showToast(error?.response?.data?.detail || "连接服务端浏览器失败", "error");
    return false;
  }
};

const openBrowserPanel = async () => {
  if (browserPanelOpening.value) return;
  if (browserSessionId.value && browserViewerToken.value) {
    browserPanelVisible.value = true;
    return;
  }
  if (!config.token) {
    showToast("浏览器需要有效的登录凭证", "warning");
    return;
  }
  const generation = ++browserOpenGeneration;
  browserPanelOpening.value = true;
  browserPanelVisible.value = true;
  browserEnvironmentError.value = null;
  try {
    const sessionResponse = await axios.post(
      "/api/v1/chat/browser/sessions/open",
      { url: "https://www.baidu.com/", conversation_id: conversationId.value || undefined },
      { headers: embedAuthHeaders() },
    );
    if (generation !== browserOpenGeneration) return;
    const session = sessionResponse.data;
    browserEnvironmentError.value = null;
    const attached = await attachBrowserSession(session.id, session.approval_mode, generation);
    if (!attached && generation === browserOpenGeneration) browserPanelVisible.value = false;
  } catch (error: any) {
    if (generation !== browserOpenGeneration) return;
    const detail = String(error?.response?.data?.detail || "");
    const isEnvironmentFailure =
      error?.response?.status === 503 || /playwright|chromium|install-deps|运行环境未就绪/i.test(detail);
    if (isEnvironmentFailure) {
      browserEnvironmentError.value = detail || "服务端浏览器环境未就绪，请检查 Playwright/Chromium 安装状态";
      browserPanelVisible.value = true;
    } else {
      browserPanelVisible.value = false;
      showToast(detail || "打开服务端浏览器失败", "error");
    }
  } finally {
    if (generation === browserOpenGeneration) browserPanelOpening.value = false;
  }
};

const handleOpenWebPreviewUrl = (url: string) => {
  if (!isBrowserOpenableUrl(url)) return;
  browserPanelVisible.value = false;
  webPreviewUrl.value = url;
  webPreviewVisible.value = true;
};

const closeWebPreviewPanel = () => {
  webPreviewVisible.value = false;
  webPreviewUrl.value = null;
};

const handleBrowserCropAskAi = async ({ image, question }: { image: string; question: string }) => {
  userInput.value = question;
  if (image && chatInputRef.value?.addBase64Image) {
    await chatInputRef.value.addBase64Image(image, `browser_crop_${Date.now()}.png`);
  }
  showToast("已自动将截图附加至提问框并填入问题，可直接发送", "success");
  nextTick(() => {
    chatInputRef.value?.focus?.();
  });
};

const closeBrowserPanel = () => {
  browserOpenGeneration += 1;
  browserPanelOpening.value = false;
  browserPanelVisible.value = false;
};

const closeBrowserSession = async (destroyProfile: boolean = false) => {
  const sessionId = browserSessionId.value;
  if (!sessionId || typeof window === "undefined") return;
  browserOpenGeneration += 1;
  browserPanelOpening.value = false;
  try {
    await axios.delete(
      `/api/v1/chat/browser/sessions/${encodeURIComponent(sessionId)}?destroy_profile=${destroyProfile ? "true" : "false"}`,
      { headers: embedAuthHeaders() },
    );
    browserPanelVisible.value = false;
    browserSessionId.value = null;
    browserViewerToken.value = null;
    if (destroyProfile) {
      showToast("浏览器会话已结束，重置登录与本地缓存成功", "success");
    } else {
      showToast("浏览器会话已结束，Profile 和 Cookie 已保留", "success");
    }
  } catch (error: any) {
    showToast(error?.response?.data?.detail || "结束浏览器会话失败", "error");
  }
};

const toggleBrowserPanel = () => {
  if (browserPanelVisible.value) {
    closeBrowserPanel();
  } else {
    void openBrowserPanel();
  }
};

const updateBrowserApprovalMode = async (mode: BrowserApprovalMode) => {
  if (!browserSessionId.value) return;
  const previous = browserApprovalMode.value;
  browserApprovalMode.value = mode;
  try {
    await axios.put(
      `/api/v1/chat/browser/sessions/${encodeURIComponent(browserSessionId.value)}/policy`,
      { approval_mode: mode },
      { headers: embedAuthHeaders() },
    );
  } catch (error: any) {
    browserApprovalMode.value = previous;
    showToast(error?.response?.data?.detail || "切换浏览器动作模式失败", "error");
  }
};
const thinkingEnableOverride = ref<boolean | null>(null);
const reasoningEffortOverride = ref<ReasoningEffort | null>(null);
const welcomeCards = ref<Array<{ icon: string; title: string; subtitle: string; prompt: string }>>([]);
const showPersonalResources = ref(false);
const personalResourcesTab = ref<PersonalResourceTab>("tokens");
const portalInboxRef = ref<{ open: () => void | Promise<void> } | null>(null);
const {
  payload: workbenchHome,
  load: loadWorkbenchHome,
  refresh: refreshWorkbenchHome,
  refreshing: workbenchHomeRefreshing,
  error: workbenchHomeError,
} = useWorkbenchHome();
const welcomePersonalResources = computed(() => {
  const items = workbenchHome.value?.personal_resources;
  const source = Array.isArray(items) && items.length
    ? items
    : workbenchHomeError.value
      ? personalResourceFallbackItems()
      : personalResourcePlaceholderItems();
  return filterEmbedWelcomePersonalResources(source);
});

const refreshWelcomePersonalResources = () => {
  void refreshWorkbenchHome();
};

const openPersonalResources = (tab: string) => {
  if (isInboxPersonalResource({ tab })) {
    void portalInboxRef.value?.open();
    return;
  }
  personalResourcesTab.value = (tab as PersonalResourceTab) || "tokens";
  showPersonalResources.value = true;
};

const handleInboxOpenSavedReport = (request: SavedReportOpenRequest) => {
  openSavedReportFromHost(request);
};

const handlePersonalResourceOpenReport = (payload: any) => {
  showPersonalResources.value = false;
  openSavedReportFromHost({
    report_id: payload?.report_id,
    run_id: payload?.run_id,
    detail_tab: payload?.detail_tab,
    run_now: payload?.run_now,
  });
};

const handlePersonalResourceOpenConversation = (payload: any) => {
  showPersonalResources.value = false;
  const conversationId =
    typeof payload === "string" ? payload : payload?.conversation_id;
  if (conversationId) {
    handleHistoryClick({ conversation_id: conversationId });
  }
};

const handlePersonalResourceOpenQuestion = (payload: {
  query: string;
  action: "send" | "fill";
}) => {
  showPersonalResources.value = false;
  void handleQuickQuestion(payload?.query, payload?.action === "fill" ? "fill" : "send");
};

const loadWelcomeCards = async (agentId?: string) => {
  const id = String(agentId || '').trim();
  if (!id) {
    welcomeCards.value = [];
    return;
  }
  try {
    const response = await axios.get(`/api/portal/agents/${encodeURIComponent(id)}/welcome-cards`);
    const cards = response.data?.cards;
    welcomeCards.value = Array.isArray(cards)
      ? cards.filter((card: any) => card?.title && card?.subtitle && card?.prompt).slice(0, 3)
      : [];
  } catch (error) {
    console.warn('Failed to load agent welcome cards', error);
    welcomeCards.value = [];
  }
};
const showConfirmModal = ref(false);

/** URL ?agent_id= 深链锁定：禁止切换专家 / 自动路由 / @提及 */
const urlPinnedAgentKey = ref("");
/** 集成方指定的 agent_id：URL、INIT_CONFIG、Ticket 均使用同一实例锁定状态。 */
const integrationAgentLockId = ref("");
const pinnedAgent = ref<any | null>(null);
const pinnedAgentId = ref("");
const pinnedAgentCapabilities = ref<string[]>([]);
const urlAgentAccessError = ref<null | {
  code: "AGENT_NOT_FOUND" | "AGENT_FORBIDDEN";
  message: string;
  agentKey: string;
  displayName?: string;
}>(null);
const isUrlAgentPinned = computed(
  () => Boolean(urlPinnedAgentKey.value) && !urlAgentAccessError.value && Boolean(pinnedAgentId.value),
);
const isRoutingSettingsLocked = computed(
  () => Boolean(integrationAgentLockId.value) && !urlAgentAccessError.value,
);
const applyIntegrationAgentLock = (agentId: string) => {
  const normalizedAgentId = String(agentId || "").trim();
  if (!normalizedAgentId) return;
  integrationAgentLockId.value = normalizedAgentId;
  config.agentId = normalizedAgentId;
  config.expertAgentId = normalizedAgentId;
  config.routingMode = "expert";
  config.overrideAgentId = "";
};
const pinnedAgentLabel = computed(() => {
  const agent = pinnedAgent.value;
  return String(agent?.display_name || agent?.name || urlPinnedAgentKey.value || "").trim();
});
/** 专家模式（URL 锁定或手动选定）时左上角展示名 */
const headerExpertLabel = computed(() => {
  if (isUrlAgentPinned.value && pinnedAgentLabel.value) {
    return pinnedAgentLabel.value;
  }
  if (config.routingMode === "expert" && config.expertAgentId) {
    const agent = currentExpertAgent.value;
    return String(agent?.display_name || agent?.name || config.expertAgentId || "").trim();
  }
  return "";
});
const agentHasCapability = (cap: string) => {
  const lockedAgent = allowedAgents.value.find(
    (agent: any) => String(agent?.id || "") === integrationAgentLockId.value,
  );
  const caps = pinnedAgentCapabilities.value.length
    ? pinnedAgentCapabilities.value
    : (Array.isArray(lockedAgent?.capabilities) ? lockedAgent.capabilities : []);
  if (!Array.isArray(caps) || caps.length === 0) return false;
  return caps.includes(cap);
};
const effectiveSlashCommands = computed(() => {
  const list = slashCommands.value || [];
  if (!isRoutingSettingsLocked.value) return list;
  return list.filter((cmd: any) => {
    if (cmd.id === DATASET_PORTAL_SYSTEM_COMMAND_ID) {
      return agentHasCapability("data_query");
    }
    if (cmd.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID) {
      return agentHasCapability("knowledge_base") && !cmd.disabled;
    }
    return true;
  });
});

const resolveUrlPinnedAgent = async (): Promise<boolean> => {
  const key = String(urlPinnedAgentKey.value || "").trim();
  if (!key) {
    urlAgentAccessError.value = null;
    pinnedAgent.value = null;
    pinnedAgentId.value = "";
    pinnedAgentCapabilities.value = [];
    return true;
  }
  try {
    const res = await axios.get(`/api/portal/agents/${encodeURIComponent(key)}/embed-access`);
    const agent = res.data;
    pinnedAgent.value = agent;
    pinnedAgentId.value = String(agent?.id || "");
    pinnedAgentCapabilities.value = Array.isArray(agent?.capabilities) ? agent.capabilities : [];
    urlAgentAccessError.value = null;
    config.agentId = pinnedAgentId.value || key;
    void loadWelcomeCards(config.agentId);
    applyIntegrationAgentLock(pinnedAgentId.value || key);
    return true;
  } catch (e: any) {
    const status = e?.response?.status;
    const body = e?.response?.data || {};
    const detailObj = (body.data && typeof body.data === "object")
      ? body.data
      : (body.detail && typeof body.detail === "object" ? body.detail : null);
    const code = detailObj?.code === "AGENT_FORBIDDEN" || status === 403
      ? "AGENT_FORBIDDEN"
      : "AGENT_NOT_FOUND";
    urlAgentAccessError.value = {
      code,
      message: String(
        detailObj?.message
        || body.message
        || (code === "AGENT_FORBIDDEN" ? "无权使用该智能体" : "智能体不存在或已停用")
      ),
      agentKey: key,
      displayName: detailObj?.display_name || detailObj?.agent_name || undefined,
    };
    pinnedAgent.value = null;
    pinnedAgentId.value = "";
    pinnedAgentCapabilities.value = [];
    integrationAgentLockId.value = "";
    return false;
  }
};

const saveRoutingSettings = () => {
    localStorage.setItem("yovole_enable_multi_agent", config.enableMultiAgent ? "1" : "0");
    localStorage.setItem("yovole_show_shortcuts", config.showShortcuts ? "1" : "0");
    localStorage.setItem("yovole_enable_sql_plan", config.enableSqlPlan ? "1" : "0");
    localStorage.setItem("yovole_override_model", config.overrideModel || "");
    localStorage.setItem("yovole_approval_mode", config.approvalMode || "ask");
    localStorage.setItem("yovole_embed_theme", config.theme || "light");
    localStorage.setItem("yovole_expand_thoughts", config.expandThoughts ? "1" : "0");
    localStorage.setItem("yovole_grounding_block_mode", config.groundingBlockMode || "strict_buffer");
    localStorage.setItem("yovole_markdown_theme", config.markdownTheme || "default");
    localStorage.setItem("yovole_hide_message_border", config.hideMessageBorder ? "1" : "0");
};
const saveRoutingPreference = async (mode: "auto" | "expert", agentId = "") => {
    if (isRoutingSettingsLocked.value) return;
    const normalizedAgentId = mode === "expert" ? String(agentId || "").trim() : "";
    try {
        await axios.put("/api/portal/portal-prefs/routing", {
            routing_mode: mode,
            expert_agent_id: normalizedAgentId,
        });
    } catch (error) {
        console.error("Failed to save routing preference to Redis", error);
        showToast("路由偏好保存失败，请稍后重试", "error");
    }
};
const handleEmbedModelSelection = (model: string) => {
    config.overrideModel = model;
    thinkingEnableOverride.value = null;
    reasoningEffortOverride.value = null;
    saveRoutingSettings();
};
const resetEmbedThinkingOverrides = () => {
    thinkingEnableOverride.value = null;
    reasoningEffortOverride.value = null;
};
const switchToAuto = () => {
    if (isRoutingSettingsLocked.value) {
      showToast("当前链接已锁定指定智能体，无法切换到智能委派", "warning");
      return;
    }
    config.routingMode = "auto";
    config.expertAgentId = "";
    void saveRoutingPreference("auto");
    showToast("已切换为智能委派模式", "success");
};
const switchToExpert = (agentId: string) => {
    if (isRoutingSettingsLocked.value) {
      showToast("当前链接已锁定指定智能体，无法切换其他专家", "warning");
      return;
    }
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) return;
    config.expertAgentId = normalizedAgentId;
    config.routingMode = "expert";
    void saveRoutingPreference("expert", normalizedAgentId);
    // URL 深链锁定初始化时不提示，避免首屏弹出
    if (!isRoutingSettingsLocked.value) {
      const agent = allowedAgents.value.find((a: any) => a.id === normalizedAgentId) || pinnedAgent.value;
      const name = agent?.display_name || agent?.name || "专家";
      showToast(`已切换至专家：${name}`, "success");
    }
};
const onModeChange = (mode: string) => {
    saveRoutingSettings();
    if (mode === "auto") {
        showToast("已切换为智能委派模式", "success");
    }
};
const conversationId = ref("");
/** 会话内各 trace_id → 产物数量 的缓存（后端 /artifacts/counts 返回），驱动「产物」按钮显示与数量角标 */
const artifactCountByTrace = ref<Record<string, number>>({});
/** 某 trace 对应的产物数量（无则 0） */
const artifactCount = (traceId: string): number => artifactCountByTrace.value[traceId] || 0;
/** 会话内可复用结果数量；只用于入口可用性，不改变当前消息角标口径。 */
const conversationReusableResultCount = ref(0);
/** 会话内是否存在可复用结果；只用于入口可用性，不改变当前消息角标口径。 */
const hasConversationReusableResult = computed(() => Boolean(
  conversationReusableResultCount.value > 0
  || messages.value.some((msg) => currentMessageReusableCount(msg) > 0),
));
/** 会话内是否存在文件产物；只用于入口可用性，不改变当前消息角标口径。 */
const hasConversationArtifact = computed(() => Object.values(artifactCountByTrace.value).some((count) => count > 0));
/** 会话内文件产物总数；用于弹出菜单右侧的会话总数，不改变当前消息角标口径。 */
const conversationArtifactCount = computed(() => Object.values(artifactCountByTrace.value).reduce((total, count) => total + count, 0));
/** “数据 / 文件”入口按整个会话的产出物判断是否可用。 */
const hasConversationDataFile = computed(() => Boolean(
  hasConversationReusableResult.value
  || hasConversationArtifact.value
  || messages.value.some((msg) => Boolean(msg.hasDataOutput)),
));
/** 拉取本会话可复用结果数量，保证刷新后入口仍能展示正确的查看项。 */
const loadReusableResultAvailability = async () => {
  const cid = conversationId.value;
  if (!cid) {
    conversationReusableResultCount.value = 0;
    reusableResultCountByTrace.value = {};
    return;
  }
  try {
    const res = await artifactApi.reusableResults(cid);
    if (conversationId.value !== cid) return;
    const items = res.data?.data?.items ?? [];
    const counts: Record<string, number> = {};
    for (const item of items) {
      const traceId = String(item.trace_id || '').trim();
      if (traceId) counts[traceId] = (counts[traceId] || 0) + 1;
    }
    conversationReusableResultCount.value = items.length;
    reusableResultCountByTrace.value = counts;
  } catch (e) {
    console.warn("[ReusableResults] 拉取会话结果数量失败", e);
    if (conversationId.value === cid) {
      conversationReusableResultCount.value = 0;
      reusableResultCountByTrace.value = {};
    }
  }
};
/** 拉取本会话各 trace_id 的产物数量（新会话/切会话/流式结束有新增产物时调用） */
const loadArtifactCounts = async () => {
  const cid = conversationId.value;
  if (!cid) {
    artifactCountByTrace.value = {};
    return;
  }
  try {
    const res = await artifactApi.countsByTrace(cid);
    // 仅在会话未切换时写入，避免旧会话的统计覆盖新会话
    if (conversationId.value !== cid) return;
    artifactCountByTrace.value = res.data?.data?.counts ?? {};
  } catch (e) {
    console.warn("[Artifacts] 拉取产物数量失败", e);
    if (conversationId.value === cid) {
      artifactCountByTrace.value = {};
    }
  }
};
// 会话切换 / 首次挂载 → 刷新产物数量
watch(conversationId, () => {
  selectedReusableResultId.value = null;
  focusedReusableResultId.value = null;
  focusedOutputTraceId.value = null;
  reusedReusableResultId.value = null;
  artifactCountByTrace.value = {};
  reusableResultCountByTrace.value = {};
  conversationReusableResultCount.value = 0;
  void loadReusableResultAvailability();
  void loadArtifactCounts();
});
const showResourceScopeModal = ref(false);
const resourceScope = ref({ project_name: '', datasets: [] as any[], knowledge_bases: [] as any[], skills: [] as any[], mcp_tools: [] as any[] });
const {
  activeMetadataDatasetIds,
  syncActiveMetadataDatasetsFromInput,
  toggleMetadataDatasetActive,
} = useDatasetMount();
const resourceScopeDraft = reactive({ project_name: '', datasets: '', knowledge_bases: '', skills: '', mcp_tools: '' });
const resourceOptionsLoading = ref(false);
const resourceOptionsLoaded = ref(false);
const resourceOptionSearch = reactive<Record<string, string>>({ datasets: '', knowledge_bases: '', skills: '', mcp_tools: '' });
type ResourceScopeGroupKey = 'datasets' | 'knowledge_bases' | 'skills' | 'mcp_tools';
const resourceOptions = reactive<Record<ResourceScopeGroupKey, any[]>>({ datasets: [], knowledge_bases: [], skills: [], mcp_tools: [] });

const emptyResourceScopeState = () => ({
  project_name: '',
  datasets: [] as any[],
  knowledge_bases: [] as any[],
  skills: [] as any[],
  mcp_tools: [] as any[],
});

const resourceOptionGroups: { key: ResourceScopeGroupKey; label: string; shortLabel?: string; hint: string }[] = [
  {
    key: 'datasets',
    label: '数据集',
    shortLabel: '数据集',
    hint: '不选则数据门户与 ChatBI 仍按默认权限；选中后仅允许所列数据集。',
  },
  {
    key: 'knowledge_bases',
    label: '知识库',
    shortLabel: '知识库',
    hint: '不选则沿用会话内已选知识库；选中后检索仅限列表内知识库。',
  },
  {
    key: 'skills',
    label: '技能 (Skills)',
    shortLabel: '技能',
    hint: '不选则仍可按问题自动匹配技能；选中后仅加载已挂载技能。',
  },
  {
    key: 'mcp_tools',
    label: '我的 MCP',
    shortLabel: 'MCP',
    hint: '仅可选择个人已发布 MCP；平台公共 MCP 请在智能体版本中配置。选中后与版本 tools 叠加注入本会话。',
  },
];
const resourceScopeModalDraft = ref(emptyResourceScopeState());
const resourceScopeSaving = ref(false);
const resourceScopeActiveTab = ref<ResourceScopeGroupKey>('datasets');
const resourceScopeCount = computed(() => resourceScope.value.datasets.length + resourceScope.value.knowledge_bases.length + resourceScope.value.skills.length + resourceScope.value.mcp_tools.length);
const projectSessionHasDatasetScope = computed(() => Boolean(resourceScope.value.project_name) && resourceScope.value.datasets.length > 0);
const projectSessionHasKnowledgeScope = computed(() => Boolean(resourceScope.value.project_name) && resourceScope.value.knowledge_bases.length > 0);
const sessionMountedMetadataDatasetIds = computed(() =>
  resourceScope.value.datasets.map((item: any) => String(item.id || '').trim()).filter(Boolean),
);
const scopedKnowledgeDatasets = computed(() => {
  if (!resourceScope.value.project_name) return knowledgeDatasets.value;
  if (!projectSessionHasKnowledgeScope.value) return knowledgeDatasets.value;
  const allowed = new Set(resourceScope.value.knowledge_bases.flatMap((item: any) => [item.id, item.name, item.dataset_id, item.ragflow_dataset_id].filter(Boolean).map((value: any) => String(value))));
  return knowledgeDatasets.value.filter((item: any) => [item.id, item.ragflow_dataset_id, item.dataset_id, item.name, item.platform_name].filter(Boolean).some((value: any) => allowed.has(String(value))));
});
const scopedActiveDatasetIds = computed(() => {
  if (!resourceScope.value.project_name) return activeDatasetIds.value;
  const allowed = new Set(scopedKnowledgeDatasets.value.map((item: any) => String(item.id || item.ragflow_dataset_id || item.dataset_id)));
  return activeDatasetIds.value.filter((id: string) => allowed.has(String(id)));
});
const scopedPortalNavigationPayload = computed(() => {
  if (!projectSessionHasDatasetScope.value || !portalNavigationPayload.value) return portalNavigationPayload.value;
  const allowed = new Set(resourceScope.value.datasets.flatMap((item: any) => [item.id, item.name, item.dataset_name, item.display_name].filter(Boolean).map((value: any) => String(value))));
  return {
    ...portalNavigationPayload.value,
    groups: (portalNavigationPayload.value.groups || []).map((group: any) => ({
      ...group,
      related_data: (group.related_data || []).filter((item: any) => [item.dataset, item.dataset_name, item.display_name, item.name, item.id].filter(Boolean).some((value: any) => allowed.has(String(value)))),
    })).filter((group: any) => (group.related_data || []).length > 0),
    dataset_count: resourceScope.value.datasets.length,
  };
});
const resourceScopeEntryKey = (type: ResourceScopeGroupKey | string, item: any, index = 0) => {
  const id = String(item?.id || item?.name || index);
  const scope = String(item?.scope || '').trim();
  return scope ? `${type}:${scope}:${id}` : `${type}:${id}`;
};

const resourceScopeEntriesMatch = (left: any, right: any) => {
  if (String(left?.id || '') !== String(right?.id || '')) return false;
  const leftScope = String(left?.scope || '').trim();
  const rightScope = String(right?.scope || '').trim();
  if (leftScope && rightScope) return leftScope === rightScope;
  return true;
};

const resourceEntryMatchesOption = (entry: any, option: any) => {
  if (entry?.scope && option?.scope && String(entry.scope) !== String(option.scope)) return false;
  const eid = String(entry?.id ?? '').trim();
  const oid = String(option?.id ?? '').trim();
  const ename = String(entry?.name ?? '').trim();
  const oname = String(option?.name ?? '').trim();
  if (eid && oid && eid === oid) return true;
  if (ename && oname && ename === oname) return true;
  if (eid && oname && eid === oname) return true;
  if (ename && oid && ename === oid) return true;
  const edn = String(entry?.dataset_name ?? '').trim();
  const odn = String(option?.dataset_name ?? option?.name ?? '').trim();
  if (edn && odn && edn === odn) return true;
  return false;
};

const remapScopeSelections = (items: any[], options: any[]) =>
  (items || []).map((selected: any) => {
    const matched = options.find((option: any) => resourceEntryMatchesOption(selected, option));
    return matched
      ? {
          ...selected,
          id: matched.id,
          name: matched.name || selected.name,
          ...(matched.dataset_name ? { dataset_name: matched.dataset_name } : {}),
        }
      : selected;
  });

const syncResourceScopeDraftStrings = (scope: typeof resourceScope.value) => {
  resourceScopeDraft.project_name = scope.project_name || '';
  resourceScopeDraft.datasets = scope.datasets.map((item: any) => item.name || item.id).join(',');
  resourceScopeDraft.knowledge_bases = scope.knowledge_bases.map((item: any) => item.name || item.id).join(',');
  resourceScopeDraft.skills = scope.skills.map((item: any) => item.name || item.id).join(',');
  resourceScopeDraft.mcp_tools = (scope.mcp_tools || []).map((item: any) => item.name || item.id).join(',');
};

const cloneResourceScope = (scope: typeof resourceScope.value) => ({
  project_name: scope.project_name || '',
  datasets: scope.datasets.map((item: any) => ({ ...item })),
  knowledge_bases: scope.knowledge_bases.map((item: any) => ({ ...item })),
  skills: scope.skills.map((item: any) => ({ ...item })),
  mcp_tools: (scope.mcp_tools || []).map((item: any) => ({ ...item })),
});

const modalDraftSelections = (type: ResourceScopeGroupKey) => resourceScopeModalDraft.value[type] || [];

const modalOrphanSelections = (type: ResourceScopeGroupKey) => {
  if (!resourceOptionsLoaded.value) return [];
  const options = resourceOptions[type] || [];
  if (!options.length) return [];
  return modalDraftSelections(type).filter((item) => !options.some((option) => resourceEntryMatchesOption(item, option)));
};

const modalResourceOrphanCount = computed(() =>
  resourceOptionGroups.reduce((sum, group) => sum + modalOrphanSelections(group.key).length, 0),
);

const modalSelectedCount = (type: ResourceScopeGroupKey) => modalDraftSelections(type).length;

const modalOptionTotalCount = (type: ResourceScopeGroupKey) => (resourceOptions[type] || []).length;

const isPersonalSkillItem = (item: any) => String(item?.scope || '').toLowerCase() === 'personal';

const modalSkillScopeSelectedCount = (scope: 'global' | 'personal') =>
  modalDraftSelections('skills').filter((item: any) =>
    scope === 'personal' ? isPersonalSkillItem(item) : !isPersonalSkillItem(item),
  ).length;

const modalSkillScopeTotalCount = (scope: 'global' | 'personal') =>
  (resourceOptions.skills || []).filter((item: any) =>
    scope === 'personal' ? isPersonalSkillItem(item) : !isPersonalSkillItem(item),
  ).length;

const modalSelectedChips = (type: ResourceScopeGroupKey) => {
  const orphans = new Set(modalOrphanSelections(type));
  return modalDraftSelections(type).map((item: any, index: number) => ({
    key: resourceScopeEntryKey(type, item, index),
    item,
    label: item.name || item.id || '未命名',
    orphan: orphans.has(item),
  }));
};

const resourceModalOptionSelected = (type: ResourceScopeGroupKey, option: any) =>
  modalDraftSelections(type).some((item) => resourceEntryMatchesOption(item, option));

const resourceOptionInitial = (option: any) => String(option.name || option.id || '?').trim().charAt(0).toUpperCase();
const resourceOptionAccent = (index: number) => ['bg-teal-500', 'bg-lime-500', 'bg-violet-500', 'bg-green-500', 'bg-sky-500'][index % 5] || 'bg-teal-500';

const filteredResourceOptions = (type: ResourceScopeGroupKey) => {
  const query = (resourceOptionSearch[type] || '').trim().toLowerCase();
  return (resourceOptions[type] || []).filter((item: any) =>
    !query
    || `${item.name || ''} ${item.id || ''} ${item.description || ''} ${item.server_name || ''} ${item.server_remark || ''}`.toLowerCase().includes(query),
  );
};

const sortedModalResourceOptions = (type: ResourceScopeGroupKey) => {
  const options = filteredResourceOptions(type);
  // MCP 按服务分组展示，组内按名称排序即可，不再把已选顶到列表最前
  if (type === 'mcp_tools') {
    return options.slice().sort((a: any, b: any) => {
      const serverCmp = String(a.server_name || '').localeCompare(String(b.server_name || ''), 'zh-CN');
      if (serverCmp !== 0) return serverCmp;
      return String(a.name || a.id || '').localeCompare(String(b.name || b.id || ''), 'zh-CN');
    });
  }
  const selected: any[] = [];
  const rest: any[] = [];
  for (const option of options) {
    if (resourceModalOptionSelected(type, option)) selected.push(option);
    else rest.push(option);
  }
  return [...selected, ...rest];
};

const buildModalDraftOptionItem = (option: any) => ({
  id: option.id,
  name: option.name || option.id,
  ...(option.dataset_name ? { dataset_name: option.dataset_name } : {}),
  ...(option.scope ? { scope: option.scope } : {}),
  ...(option.server_name ? { server_name: option.server_name } : {}),
  ...(option.server_remark ? { server_remark: option.server_remark } : {}),
  ...(option.description ? { description: option.description } : {}),
});

const toggleModalResourceOption = (type: ResourceScopeGroupKey, option: any) => {
  const selected = resourceModalOptionSelected(type, option);
  const items = selected
    ? modalDraftSelections(type).filter((item) => !resourceEntryMatchesOption(item, option))
    : [
        ...modalDraftSelections(type),
        buildModalDraftOptionItem(option),
      ];
  resourceScopeModalDraft.value = { ...resourceScopeModalDraft.value, [type]: items };
};

const toggleModalResourceGroup = (type: ResourceScopeGroupKey, options: any[], selectAll: boolean) => {
  let items = [...modalDraftSelections(type)];
  for (const option of options || []) {
    const isSelected = items.some((item) => resourceEntryMatchesOption(item, option));
    if (selectAll && !isSelected) {
      items.push(buildModalDraftOptionItem(option));
    } else if (!selectAll && isSelected) {
      items = items.filter((item) => !resourceEntryMatchesOption(item, option));
    }
  }
  resourceScopeModalDraft.value = { ...resourceScopeModalDraft.value, [type]: items };
};

const removeModalDraftResource = (type: ResourceScopeGroupKey, item: any) => {
  const items = modalDraftSelections(type).filter((entry) => entry !== item && !resourceScopeEntriesMatch(entry, item));
  resourceScopeModalDraft.value = { ...resourceScopeModalDraft.value, [type]: items };
};

const syncResourceScopeActiveTabForDraft = () => {
  const withOrphans = resourceOptionGroups.find((group) => modalOrphanSelections(group.key).length > 0);
  if (withOrphans) {
    resourceScopeActiveTab.value = withOrphans.key;
    return;
  }
  const withSelection = resourceOptionGroups.find((group) => modalSelectedCount(group.key) > 0);
  resourceScopeActiveTab.value = withSelection?.key ?? 'datasets';
};

const loadResourceOptions = async () => {
  resourceOptionsLoading.value = true;
  try {
    const [datasets, knowledge, globalSkills, personalSkills, mcpTools] = await Promise.allSettled([
      axios.get('/api/portal/metadata/datasets/accessible'),
      axios.get('/api/portal/ragflow/datasets', { params: { page: 1, page_size: 100, include_missing: false } }),
      axios.get('/api/portal/skills'),
      axios.get('/api/portal/skills/personal'),
      axios.get('/api/portal/tools/mcp'),
    ]);
    if (datasets.status === 'fulfilled') {
      const raw = datasets.value.data;
      const list = Array.isArray(raw) ? raw : (raw?.data || raw?.datasets || []);
      resourceOptions.datasets = list
        .filter((item: any) => item.status === undefined || item.status === 1 || item.status === '1' || item.status === 'active')
        .map((item: any) => ({
          id: String(item.id || item.name),
          name: item.display_name || item.name || item.dataset_name,
          dataset_name: item.name || item.dataset_name,
          description: item.description || item.remark || item.notes || `数据源：${item.data_source || '默认'}`,
        }));
    }
    if (knowledge.status === 'fulfilled') {
      const data = knowledge.value.data?.data;
      const list = Array.isArray(data) ? data : (data?.datasets || data?.items || []);
      resourceOptions.knowledge_bases = list
        .filter((item: any) => item.status === undefined || item.status === 'active' || item.status === 1 || item.status === '1')
        .map((item: any) => ({
          id: String(item.id || item.dataset_id),
          name: item.name || item.display_name || item.dataset_name,
          dataset_name: item.id || item.dataset_id,
          description: item.description || item.summary || item.notes || '暂无知识库描述',
        }));
    }
    resourceOptions.skills = [];
    for (const [result, scope] of [[globalSkills, 'global'], [personalSkills, 'personal']] as const) {
      if (result.status === 'fulfilled') resourceOptions.skills.push(...(result.value.data?.data || [])
        .filter((item: any) => item.enabled === undefined || item.enabled === true || item.enabled === 'true' || item.enabled === 1 || item.enabled === '1')
        .map((item: any) => ({ id: String(item.id), name: item.name, description: item.description, scope })));
    }
    if (mcpTools.status === 'fulfilled') {
      const raw = mcpTools.value.data;
      const list = Array.isArray(raw) ? raw : (raw?.data || []);
      resourceOptions.mcp_tools = list
        .map((item: any) => ({
          id: String(item.id || ''),
          name: String(item.name || ''),
          description: item.description || '',
          server_name: item.server_name || '',
          server_remark: item.server_remark || '',
          scope: item.scope || 'global',
        }))
        .filter((item: any) => item.id && item.name && String(item.scope || '').toLowerCase() === 'personal');
    } else {
      resourceOptions.mcp_tools = [];
    }
    for (const group of resourceOptionGroups) {
      const options = resourceOptions[group.key] || [];
      const key = group.key;
      resourceScope.value[key] = remapScopeSelections(resourceScope.value[key], options);
      if (showResourceScopeModal.value) {
        resourceScopeModalDraft.value[key] = remapScopeSelections(resourceScopeModalDraft.value[key], options);
      }
    }
    resourceOptionsLoaded.value = true;
  } finally {
    resourceOptionsLoading.value = false;
  }
};

/** 仅拉元数据集，供数据门户挂载匹配；避免顺带等待 RAGFlow/Skills。 */
const ensureMountableMetadataDatasets = async () => {
  if (resourceOptions.datasets.length) return;
  try {
    const res = await axios.get('/api/portal/metadata/datasets/accessible');
    const raw = res.data;
    const list = Array.isArray(raw) ? raw : (raw?.data || raw?.datasets || []);
    resourceOptions.datasets = list
      .filter((item: any) => item.status === undefined || item.status === 1 || item.status === '1' || item.status === 'active')
      .map((item: any) => ({
        id: String(item.id || item.name),
        name: item.display_name || item.name || item.dataset_name,
        dataset_name: item.name || item.dataset_name,
        description: item.description || item.remark || item.notes || `数据源：${item.data_source || '默认'}`,
      }));
  } catch (error) {
    console.warn('Failed to load mountable metadata datasets', error);
  }
};

const closeResourceScopeModal = () => {
  if (resourceScopeSaving.value) return;
  showResourceScopeModal.value = false;
};

const openResourceScopeModal = () => {
  resourceScopeModalDraft.value = cloneResourceScope(resourceScope.value);
  syncResourceScopeActiveTabForDraft();
  showResourceScopeModal.value = true;
  if (!resourceOptionsLoaded.value) void loadResourceOptions();
};

const refreshResourceOptions = async () => {
  resourceOptionsLoaded.value = false;
  await loadResourceOptions();
};

const loadResourceScope = async () => {
  if (!conversationId.value) return;
  const requestedId = conversationId.value;
  const requestId = ++resourceScopeLoadSequence;
  try {
    const res = await axios.get(`/api/v1/chat/conversation/${encodeURIComponent(requestedId)}/resource-scope`, { headers: embedAuthHeaders() });
    if (requestId !== resourceScopeLoadSequence || conversationId.value !== requestedId) return;
    resourceScope.value = {
      ...emptyResourceScopeState(),
      ...(res.data?.data || {}),
      mcp_tools: Array.isArray(res.data?.data?.mcp_tools) ? res.data.data.mcp_tools : [],
    };
    syncResourceScopeDraftStrings(resourceScope.value);
  } catch (error) { console.warn('[ResourceScope] load failed', error); }
};

const buildPersistableScope = (source: typeof resourceScope.value) => {
  const normalizeItems = (items: any[]) => items
    .filter((item) => item?.id !== undefined && item?.id !== null && String(item.id).trim())
    .map((item) => ({
      id: String(item.id).trim(),
      name: item.name || String(item.id).trim(),
      ...(item.dataset_name ? { dataset_name: item.dataset_name } : {}),
      ...(item.scope ? { scope: item.scope } : {}),
      ...(item.description ? { description: item.description } : {}),
      ...(item.server_name ? { server_name: item.server_name } : {}),
      ...(item.server_remark ? { server_remark: item.server_remark } : {}),
    }));
  return {
    project_name: (source.project_name || '').trim(),
    datasets: normalizeItems(source.datasets),
    knowledge_bases: normalizeItems(source.knowledge_bases),
    skills: normalizeItems(source.skills),
    mcp_tools: normalizeItems(source.mcp_tools || []),
  };
};

const persistResourceScope = async (scope: ReturnType<typeof buildPersistableScope>) => {
  const res = await axios.put(
    `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
    scope,
    { headers: embedAuthHeaders() },
  );
  const saved = res.data?.data || scope;
  resourceScope.value = saved;
  syncResourceScopeDraftStrings(saved);
  return saved;
};

const mountMcpToolToSession = async (toolsInput: Array<{ id: string; name: string; description?: string; server_name?: string; server_remark?: string; scope?: string }> | { id: string; name: string; description?: string; server_name?: string; server_remark?: string; scope?: string }) => {
  if (!conversationId.value) {
    showToast('请先开始会话', 'error');
    return;
  }
  const tools = (Array.isArray(toolsInput) ? toolsInput : [toolsInput])
    .map((tool) => ({
      id: String(tool?.id || '').trim(),
      name: String(tool?.name || '').trim(),
      description: tool?.description || '',
      server_name: tool?.server_name || '',
      server_remark: tool?.server_remark || '',
      scope: tool?.scope || 'global',
    }))
    .filter((tool) => tool.id && tool.name);
  if (!tools.length) return;

  const existing = resourceScope.value.mcp_tools || [];
  const existingNames = new Set(existing.map((item: any) => String(item.name || '').trim()).filter(Boolean));
  const existingIds = new Set(existing.map((item: any) => String(item.id || '').trim()).filter(Boolean));
  const toAdd = tools.filter((tool) => !existingNames.has(tool.name) && !existingIds.has(tool.id));
  if (!toAdd.length) {
    showToast(tools.length === 1 ? '该 MCP 工具已挂载到本会话' : '所选 MCP 工具均已挂载到本会话', 'info');
    return;
  }

  const nextScope = {
    ...resourceScope.value,
    mcp_tools: [...existing, ...toAdd],
  };
  resourceScopeSaving.value = true;
  try {
    await persistResourceScope(buildPersistableScope(nextScope));
    showToast(
      toAdd.length === 1 ? `已挂载 MCP 工具：${toAdd[0]?.name || ''}` : `已挂载 ${toAdd.length} 个 MCP 工具`,
      'success',
    );
  } catch (error) {
    showToast('挂载 MCP 工具失败', 'error');
  } finally {
    resourceScopeSaving.value = false;
  }
};

const pinMetadataDatasetToSession = async (datasetId: string) => {
  if (!conversationId.value) {
    showToast('请先开始会话', 'error');
    return;
  }
  const id = String(datasetId || '').trim();
  if (!id) return;
  if (resourceScope.value.datasets.some((item: any) => String(item.id) === id)) {
    showToast('该数据集已设为本会话默认', 'info');
    return;
  }
  const selected =
    (resourceOptions.datasets || []).find((item: any) => String(item.id) === id) || { id, name: id };
  const nextScope = {
    ...resourceScope.value,
    datasets: [...resourceScope.value.datasets, selected],
  };
  resourceScopeSaving.value = true;
  try {
    await persistResourceScope(buildPersistableScope(nextScope));
    const dsName = selected.name || selected.display_name || selected.dataset_name || id;
    showToast(`已设为本会话默认：后续提问将默认锁定在【${dsName}】`, 'success');
  } catch (error) {
    showToast('设置会话默认范围失败', 'error');
  } finally {
    resourceScopeSaving.value = false;
  }
};

const unpinMetadataDatasetFromSession = async (datasetId: string) => {
  if (!conversationId.value) {
    showToast('请先开始会话', 'error');
    return;
  }
  const id = String(datasetId || '').trim();
  if (!id) return;
  const nextScope = {
    ...resourceScope.value,
    datasets: resourceScope.value.datasets.filter((item: any) => String(item.id) !== id),
  };
  resourceScopeSaving.value = true;
  try {
    await persistResourceScope(buildPersistableScope(nextScope));
    showToast('已取消本会话默认，问数将恢复默认权限范围', 'info');
  } catch (error) {
    showToast('取消会话默认失败', 'error');
  } finally {
    resourceScopeSaving.value = false;
  }
};

const saveResourceScope = async () => {
  if (!conversationId.value) {
    showToast('请先开始会话', 'error');
    return;
  }
  const draft = resourceScopeModalDraft.value;
  if (!draft.project_name.trim()) {
    showToast('请先填写项目名称', 'warning');
    return;
  }
  resourceScopeSaving.value = true;
  try {
    await persistResourceScope(buildPersistableScope(draft));
    showResourceScopeModal.value = false;
    showToast('项目会话资源已保存', 'success');
  } catch (error) {
    showToast('资源范围更新失败', 'error');
  } finally {
    resourceScopeSaving.value = false;
  }
};

let requestedConversationId = "";
let resourceScopeLoadSequence = 0;
let initConfigReceived = false;
let pendingUrlTokenInitTimer: number | null = null;
let conversationInitializationGeneration = 0;
const LEGACY_CONVERSATION_STORAGE_KEY = "yovole_embed_conv_id";
const INSTANCE_CONVERSATION_STORAGE_PREFIX = "yovole_embed_conv_id:";

const normalizeEmbedInstanceId = (value: unknown): string => {
  const normalized = String(value ?? "").trim();
  return normalized;
};

const conversationStorageKey = () =>
  config.instanceId
    ? `${INSTANCE_CONVERSATION_STORAGE_PREFIX}${encodeURIComponent(config.instanceId)}`
    : LEGACY_CONVERSATION_STORAGE_KEY;

const readStoredConversationId = () => localStorage.getItem(conversationStorageKey());

const persistConversationId = (cid: string) => {
  if (cid) localStorage.setItem(conversationStorageKey(), cid);
};

const shouldUseServerActiveConversation = () => Boolean(config.token);
const activeConversationRequestParams = () => (
  config.instanceId ? { instance_id: config.instanceId } : undefined
);

const cancelPendingUrlTokenInitialization = () => {
  if (pendingUrlTokenInitTimer === null) return;
  window.clearTimeout(pendingUrlTokenInitTimer);
  pendingUrlTokenInitTimer = null;
};

const scheduleUrlTokenInitialization = () => {
  if (!config.token || initConfigReceived) return;
  cancelPendingUrlTokenInitialization();
  if (config.instanceId) {
    void initChat();
    return;
  }
  // 给父页面一个握手窗口，让 INIT_CONFIG 中的 instance_id 先于 URL token 初始化生效。
  pendingUrlTokenInitTimer = window.setTimeout(() => {
    pendingUrlTokenInitTimer = null;
    if (!initConfigReceived && config.token) void initChat();
  }, 250);
};

const embedAuthHeaders = (): Record<string, string> | undefined => {
  if (!config.token) return undefined;
  return {
    Authorization: `Bearer ${config.token}`,
    "X-API-Key": config.token,
  };
};

const {
  remoteRunActive,
  refresh: refreshRemoteRunStatus,
  stopPolling: stopRemoteRunPolling,
  markOutputCompleted,
} = useConversationRunStatus(async (cid) => {
  const response = await axios.get(
    `/api/v1/chat/conversation/${encodeURIComponent(cid)}/run-status`,
    { headers: embedAuthHeaders() },
  );
  return response.data?.data || {};
});

const refreshCurrentRunStatus = () => (
  conversationId.value
    ? refreshRemoteRunStatus(conversationId.value)
    : Promise.resolve(false)
);

const focusChatInputWhenReady = () => {
  if (isMobile.value || isProcessing.value || remoteRunActive.value || sendLocked.value) return;
  nextTick(() => chatInputRef.value?.focus());
};

watch([isProcessing, remoteRunActive, sendLocked], focusChatInputWhenReady);

watch(conversationId, () => {
  void refreshCurrentRunStatus();
}, { immediate: true });

const DOCKER_WORKSPACE_BANNER_DISMISSED_KEY = "nanzi_dismissed_docker_workspace_banner";

const readDockerWorkspaceBannerDismissed = (): boolean => {
  try {
    return localStorage.getItem(DOCKER_WORKSPACE_BANNER_DISMISSED_KEY) === "true";
  } catch {
    return false;
  }
};

const { contextUsage, refreshContextUsage } = useContextUsage();
type DockerWorkspaceStatus = "idle" | "starting" | "stopping" | "running" | "error";
const dockerWorkspaceStatus = ref<DockerWorkspaceStatus>("idle");
const dockerWorkspaceStatusLoaded = ref(false);
const dockerWorkspaceError = ref("");
const dockerWorkspaceContainerId = ref<string | null>(null);
const dockerWorkspaceStartedAt = ref<string | null>(null);
const dockerWorkspaceUptimeSeconds = ref<number | null>(null);
const dockerWorkspaceBannerDismissed = ref(readDockerWorkspaceBannerDismissed());
const effectiveSandboxPolicy = computed(() => (
  String(contextUsage.value?.sandbox_policy || "").trim().toLowerCase()
));
const showDockerWorkspaceControl = computed(() => {
  if (effectiveSandboxPolicy.value !== "docker" || !conversationId.value) {
    return false;
  }
  // 首次状态查询完成前静默，彻底消除页面刷新时的横条闪烁
  if (!dockerWorkspaceStatusLoaded.value) {
    return false;
  }
  // 正常运行态自动隐藏（状态收拢至输入框浮标）
  if (dockerWorkspaceStatus.value === "running") {
    return false;
  }
  // 异常态强制显示，方便用户排查
  if (dockerWorkspaceStatus.value === "error") {
    return true;
  }
  // 其他状态（idle / starting）在有效 docker 策略下尊重用户的关闭偏好
  return effectiveSandboxPolicy.value === "docker" && !dockerWorkspaceBannerDismissed.value;
});

const resetDockerWorkspaceState = () => {
  dockerWorkspaceStatus.value = "idle";
  dockerWorkspaceStatusLoaded.value = false;
  dockerWorkspaceError.value = "";
  dockerWorkspaceContainerId.value = null;
  dockerWorkspaceStartedAt.value = null;
  dockerWorkspaceUptimeSeconds.value = null;
  dockerWorkspaceBannerDismissed.value = readDockerWorkspaceBannerDismissed();
};

const dismissDockerWorkspaceBanner = () => {
  dockerWorkspaceBannerDismissed.value = true;
  try {
    localStorage.setItem(DOCKER_WORKSPACE_BANNER_DISMISSED_KEY, "true");
  } catch {}
};

const refreshDockerWorkspaceStatus = async (showFeedback = false) => {
  if (effectiveSandboxPolicy.value !== "docker" || !conversationId.value) return;
  if (dockerWorkspaceStatus.value === "starting") return;
  const requestedConversationId = conversationId.value;
  try {
    const response = await axios.get(
      "/api/v1/sandbox/docker/workspace/status",
      {
        params: { conversation_id: requestedConversationId },
        headers: embedAuthHeaders(),
      },
    );
    if (conversationId.value !== requestedConversationId) return;
    const data = response.data?.data ?? response.data;
    if (data?.execution_backend !== "docker") {
      throw new Error("Docker 沙箱状态返回了错误的执行后端");
    }
    dockerWorkspaceContainerId.value = data.container_id || null;
    dockerWorkspaceStartedAt.value = data.started_at || null;
    dockerWorkspaceUptimeSeconds.value = typeof data.uptime_seconds === "number" ? data.uptime_seconds : null;
    dockerWorkspaceStatus.value = data.status === "running" ? "running" : "idle";
    dockerWorkspaceError.value = "";
    if (showFeedback) {
      if (dockerWorkspaceStatus.value === "running") {
        const shortId = (dockerWorkspaceContainerId.value || "").slice(0, 12);
        showToast(shortId ? `Docker 沙箱运行中 (${shortId})` : "Docker 沙箱运行中", "success");
      } else {
        showToast("Docker 沙箱状态已刷新：容器未启动", "info");
      }
    }
  } catch (error: any) {
    if (conversationId.value !== requestedConversationId) return;
    const detail = error?.response?.data?.detail;
    dockerWorkspaceError.value = typeof detail === "string"
      ? detail
      : String(detail?.message || error?.message || "Docker 沙箱状态查询失败");
    dockerWorkspaceStatus.value = "error";
    dockerWorkspaceStartedAt.value = null;
    dockerWorkspaceUptimeSeconds.value = null;
    if (showFeedback) {
      showToast(dockerWorkspaceError.value, "error");
    }
  } finally {
    if (conversationId.value === requestedConversationId) {
      dockerWorkspaceStatusLoaded.value = true;
    }
  }
};

const ensureDockerWorkspace = async () => {
  if (effectiveSandboxPolicy.value !== "docker" || !conversationId.value) return;
  if (dockerWorkspaceStatus.value === "starting") return;
  const requestedConversationId = conversationId.value;
  dockerWorkspaceStatus.value = "starting";
  dockerWorkspaceError.value = "";
  try {
    const response = await axios.post(
      "/api/v1/sandbox/docker/workspace/ensure",
      { conversation_id: requestedConversationId },
      { headers: embedAuthHeaders() },
    );
    if (conversationId.value !== requestedConversationId) return;
    const data = response.data?.data ?? response.data;
    if (data?.execution_backend !== "docker" || data?.status !== "running") {
      throw new Error("Docker 沙箱容器未返回运行中状态");
    }
    dockerWorkspaceStatus.value = "running";
    dockerWorkspaceContainerId.value = data.container_id || null;
    dockerWorkspaceStartedAt.value = data.started_at || null;
    dockerWorkspaceUptimeSeconds.value = typeof data.uptime_seconds === "number" ? data.uptime_seconds : 0;
    showToast("Docker 沙箱容器已启动", "success");
  } catch (error: any) {
    if (conversationId.value !== requestedConversationId) return;
    const detail = error?.response?.data?.detail;
    dockerWorkspaceError.value = typeof detail === "string"
      ? detail
      : String(detail?.message || error?.message || "Docker 沙箱容器启动失败");
    dockerWorkspaceStatus.value = "error";
    showToast(dockerWorkspaceError.value, "error");
  }
};

const showDockerTerminal = ref(false);

const openDockerTerminal = () => {
  if (dockerWorkspaceStatus.value !== "running") {
    showToast("Docker 容器未在运行中，请先启动容器", "warning");
    return;
  }
  showDockerTerminal.value = true;
};

const stopDockerWorkspace = async () => {
  if (effectiveSandboxPolicy.value !== "docker" || !conversationId.value) return;
  if (dockerWorkspaceStatus.value === "stopping" || dockerWorkspaceStatus.value === "starting") return;
  const requestedConversationId = conversationId.value;
  dockerWorkspaceStatus.value = "stopping";
  dockerWorkspaceError.value = "";
  try {
    await axios.post(
      "/api/v1/sandbox/docker/workspace/stop",
      { conversation_id: requestedConversationId },
      { headers: embedAuthHeaders() },
    );
    if (conversationId.value !== requestedConversationId) return;
    dockerWorkspaceStatus.value = "idle";
    dockerWorkspaceContainerId.value = null;
    dockerWorkspaceStartedAt.value = null;
    dockerWorkspaceUptimeSeconds.value = null;
    dockerWorkspaceError.value = "";
    showToast("Docker 沙箱容器已关机停止", "info");
  } catch (error: any) {
    if (conversationId.value !== requestedConversationId) return;
    const detail = error?.response?.data?.detail;
    const msg = typeof detail === "string"
      ? detail
      : String(detail?.message || error?.message || "停止 Docker 容器失败");
    dockerWorkspaceStatus.value = "running";
    showToast(msg, "error");
  }
};

const restartDockerWorkspace = async () => {
  if (effectiveSandboxPolicy.value !== "docker" || !conversationId.value) return;
  if (dockerWorkspaceStatus.value === "starting") return;
  const requestedConversationId = conversationId.value;
  dockerWorkspaceStatus.value = "starting";
  dockerWorkspaceError.value = "";
  try {
    const response = await axios.post(
      "/api/v1/sandbox/docker/workspace/restart",
      { conversation_id: requestedConversationId },
      { headers: embedAuthHeaders() },
    );
    if (conversationId.value !== requestedConversationId) return;
    const data = response.data?.data ?? response.data;
    dockerWorkspaceStatus.value = "running";
    dockerWorkspaceContainerId.value = data.container_id || null;
    dockerWorkspaceStartedAt.value = data.started_at || null;
    dockerWorkspaceUptimeSeconds.value = typeof data.uptime_seconds === "number" ? data.uptime_seconds : 0;
    const shortId = (dockerWorkspaceContainerId.value || "").slice(0, 12);
    showToast(shortId ? `Docker 沙箱已重启 (${shortId})` : "Docker 沙箱已重启", "success");
  } catch (error: any) {
    if (conversationId.value !== requestedConversationId) return;
    const detail = error?.response?.data?.detail;
    dockerWorkspaceError.value = typeof detail === "string"
      ? detail
      : String(detail?.message || error?.message || "Docker 沙箱重启失败");
    dockerWorkspaceStatus.value = "error";
    showToast(dockerWorkspaceError.value, "error");
  }
};

watch(
  [conversationId, effectiveSandboxPolicy],
  ([conversation, policy], previous) => {
    const previousConversation = String(previous?.[0] || "");
    const previousPolicy = String(previous?.[1] || "");
    if (
      policy !== "docker"
      || !conversation
      || conversation !== previousConversation
      || policy !== previousPolicy
    ) {
      resetDockerWorkspaceState();
      if (policy === "docker" && conversation) {
        void refreshDockerWorkspaceStatus();
      }
    }
  },
  { immediate: true },
);
const {
  contextCompactions,
  contextCompactionCount,
  contextCompactionsLoading,
  contextCompactionsError,
  contextCompactionActionLoading,
  manuallyCompactContext,
  refreshContextCompactions,
} = useContextCompactions();
const refreshEmbedContextUsage = () => refreshContextUsage({
  conversationId: conversationId.value,
  modelId: config.overrideModel || undefined,
  headers: embedAuthHeaders(),
});
const refreshEmbedContextCompactions = (force = false) => refreshContextCompactions({
  conversationId: conversationId.value,
  headers: embedAuthHeaders(),
}, force);
const manualCompactEmbedContext = async (retainRatio: 0.25 | 0.5 | 0.75 = 0.5, mode: "fast" | "smart" = "fast") => {
  try {
    const result = await manuallyCompactContext({ conversationId: conversationId.value, headers: embedAuthHeaders(), retainRatio, mode });
    await refreshContextUsage({ conversationId: conversationId.value, modelId: config.overrideModel || undefined, headers: embedAuthHeaders() });
    showToast(result?.compacted ? `上下文已压缩，预计节省 ${Number(result.saved_percent || 0)}%` : "当前没有可压缩的历史内容", result?.compacted ? "success" : "info");
  } catch {
    showToast("上下文压缩失败，请稍后重试", "error");
  }
};

watch(
  [conversationId, () => config.overrideModel, () => config.token],
  () => void refreshEmbedContextUsage(),
  { immediate: true },
);

watch(
  [conversationId, () => config.overrideModel, () => config.token],
  () => void refreshEmbedContextCompactions(true),
  { immediate: true },
);

const finalizeConversationInBackground = (cid: string) => {
  void finalizeConversation(cid, embedAuthHeaders());
};

const updateActiveConversationOnServer = async (cid: string) => {
  if (!config.token) return;
  if (!shouldUseServerActiveConversation()) return;
  try {
    await axios.post("/api/v1/chat/active", {
      conversation_id: cid
    }, {
      params: activeConversationRequestParams(),
      headers: embedAuthHeaders()
    });
  } catch (e: any) {
    console.warn("[ActiveConv] Failed to update active conversation on server:", e);
  }
};

watch(conversationId, () => {
  void loadResourceScope();
});

const generateNewConversation = () => {
  const previousId = conversationId.value;
  if (previousId) {
    finalizeConversationInBackground(previousId);
  }
  // 工作台等入口通过 INIT_CONFIG 写入的 resume id，新会话时必须清掉，
  // 否则随后 initChat() 会再次强制切回旧会话并重载历史。
  requestedConversationId = "";
  resetEmbedThinkingOverrides();
  resourceScopeLoadSequence += 1;
  conversationId.value = createConversationId();
  resourceScope.value = emptyResourceScopeState();
  Object.assign(resourceScopeDraft, { project_name: '', datasets: '', knowledge_bases: '', skills: '', mcp_tools: '' });
  persistConversationId(conversationId.value);
  updateActiveConversationOnServer(conversationId.value);
  loadResourceScope();
};
// Mention State (Moved to ChatInput)
// const showMentionList = ref(false); // Removed
// const mentionKeyword = ref(""); // Removed
// const mentionPosition = reactive({ top: 0, left: 0 }); // Removed
type RoutingMode = "auto" | "expert";
type RoutingPreference = {
  routing_mode: RoutingMode;
  expert_agent_id: string;
  routing_configured: boolean;
};
const savedRoutingPreference = ref<RoutingPreference>({
  routing_mode: "auto",
  expert_agent_id: "",
  routing_configured: false,
});

const fetchUserPortalPreferences = async () => {
    try {
        const res = await axios.get("/api/portal/portal-prefs");
        const prefs = res.data?.data || {};
        if (prefs.markdown_theme) {
            config.markdownTheme = prefs.markdown_theme;
            localStorage.setItem("user_has_custom_theme", "true");
        } else {
            localStorage.removeItem("user_has_custom_theme");
        }
        const routingMode: RoutingMode = prefs.routing_mode === "expert" ? "expert" : "auto";
        savedRoutingPreference.value = {
        routing_mode: routingMode,
        expert_agent_id: String(prefs.expert_agent_id || "").trim(),
        routing_configured: prefs.routing_configured === true,
      };
    } catch (error) {
        console.warn("Failed to fetch user portal preferences from Redis", error);
        savedRoutingPreference.value = {
          routing_mode: "auto",
          expert_agent_id: "",
          routing_configured: false,
        };
    }
};

const hasCustomMessageBorderPreference = () =>
    localStorage.getItem("user_has_custom_border_preference") === "true";

const allowedAgents = ref<any[]>([]);
const isGeneralAgentMessage = (msg: Message): boolean => {
  if (msg.agentType) return msg.agentType === "GENERAL";
  const agent = allowedAgents.value.find(
    (item: any) => item.name === msg.agentName || item.id === msg.agentName,
  );
  return agent?.agent_type === "GENERAL";
};
const hasFetchedAgents = ref(false);
const isLoadingAgents = ref(false);
const fetchAllowedAgents = async (force = false) => {
    if (hasFetchedAgents.value && !force) return;
    isLoadingAgents.value = true;
    try {
        // 先获取用户在后端 Redis 持久化的排版与路由偏好
        await fetchUserPortalPreferences();

        const res = await axios.get("/api/portal/agents/allowed");
        if (res.data) {
            allowedAgents.value = res.data; // Already filtered by backend
            hasFetchedAgents.value = true;

            // 集成锁定优先于用户 Redis 偏好；普通 Embed 只应用当前用户有权限的默认智能体。
            if (!isRoutingSettingsLocked.value) {
                const saved = savedRoutingPreference.value;
                const savedAgentAllowed = saved.expert_agent_id
                    && res.data.some((agent: any) => String(agent.id) === saved.expert_agent_id);
                if (saved.routing_configured && saved.routing_mode === "expert" && savedAgentAllowed) {
                    config.routingMode = "expert";
                    config.expertAgentId = saved.expert_agent_id;
                } else {
                    config.routingMode = "auto";
                    config.expertAgentId = "";
                }
            }
            
            // 自动应用当前激活智能体推荐的排版风格
            if (config.expertAgentId) {
                const currentAgent = res.data.find((a: any) => a.id === config.expertAgentId);
                const hasCustomTheme = localStorage.getItem("user_has_custom_theme") === "true";
                if (!hasCustomTheme) {
                    const recommendedTheme = currentAgent?.engine_config?.default_markdown_theme;
                    if (recommendedTheme) {
                        config.markdownTheme = recommendedTheme;
                    }
                }
                if (!hasCustomMessageBorderPreference()) {
                    config.hideMessageBorder = currentAgent?.engine_config?.hide_message_border ?? true;
                }
            }
            
            console.log(`[LifeCycle] Successfully fetched ${res.data.length} allowed agents.`);
            void prefetchPortalNavigationIfEligible();
        }
    } catch (e) {
        console.warn("Mention feature disabled: Cannot fetch allowed agents", e);
        hasFetchedAgents.value = false;
    } finally {
        isLoadingAgents.value = false;
    }
};

// Auto-fetch agents and commands when token becomes available
watch(() => config.token, (newToken) => {
    if (newToken) {
        console.log("[LifeCycle] Token detected/changed, re-fetching context...");
        fetchAllowedAgents(true);
        fetchSlashCommands();
        // 刷新后专家选择会先从本地恢复，此时需要在 token 到位后再读取其欢迎卡片。
        void loadWelcomeCards(effectiveEmbedChatAgentId.value);
        void loadWorkbenchHome();
    }
}, { immediate: true });

// 非 URL 深链的专家切换也需要同步刷新版本级欢迎卡片。
watch(effectiveEmbedChatAgentId, (agentId) => {
    if (config.token) {
        void loadWelcomeCards(agentId);
    }
}, { immediate: true });

// 监听当前激活的智能体变更，自动应用其配置的推荐排版风格
watch(() => config.expertAgentId, (newAgentId) => {
    if (newAgentId && allowedAgents.value.length > 0) {
        const currentAgent = allowedAgents.value.find(a => a.id === newAgentId);
        const hasCustomTheme = localStorage.getItem("user_has_custom_theme") === "true";
        if (!hasCustomTheme) {
            const recommendedTheme = currentAgent?.engine_config?.default_markdown_theme;
            if (recommendedTheme) {
                config.markdownTheme = recommendedTheme;
            } else {
                config.markdownTheme = "default";
            }
        }
        if (!hasCustomMessageBorderPreference()) {
            config.hideMessageBorder = currentAgent?.engine_config?.hide_message_border ?? true;
        }
    }
}, { immediate: true });

const handleSwitchMode = (agent: any) => {
    if (isRoutingSettingsLocked.value) {
      showToast("当前链接已锁定指定智能体，无法切换其他专家", "warning");
      return;
    }
    config.overrideAgentId = "";
    switchToExpert(agent.id);
};

const listDataQueryAgents = () => {
    return allowedAgents.value.filter((agent: any) => {
        const capabilities = Array.isArray(agent?.capabilities) ? agent.capabilities : [];
        if (capabilities.includes("data_query")) return true;
        const label = `${agent?.name || ""} ${agent?.display_name || ""} ${agent?.description || ""}`;
        return /数据查询|ChatBI|DataQuery/i.test(label);
    });
};

/** 仅当恰好 1 个查数智能体时返回，多个则不自动锁定 */
const findUniqueDataQueryAgent = () => {
    const matches = listDataQueryAgents();
    return matches.length === 1 ? matches[0] : undefined;
};

const hasDataQueryAgent = () => listDataQueryAgents().length > 0;

/** ChatBI「继续分析」等追问：本轮强制走查数智能体，避免自动路由到主助手 */
const forceDataQueryAgentOnce = ref(false);
const resolvePreferredDataQueryAgentId = () => {
  const unique = findUniqueDataQueryAgent();
  if (unique?.id) return String(unique.id);
  const first = listDataQueryAgents()[0];
  return first?.id ? String(first.id) : "";
};
const armDataQueryAgentForFollowup = () => {
  if (isRoutingSettingsLocked.value) return false;
  const agentId = resolvePreferredDataQueryAgentId();
  if (!agentId) {
    showToast("未找到可用的数据查询智能体，无法继续可视化分析", "warning");
    return false;
  }
  forceDataQueryAgentOnce.value = true;
  return true;
};
const handleChatBIContinueSelect = (query: string) => {
  if (!armDataQueryAgentForFollowup()) return;
  void handleQuickQuestion(query);
};

const handleReorderCommands = async (reorderData: any[]) => {
    try {
        await axios.post("/api/portal/slash-commands/reorder", { items: reorderData });
        await fetchSlashCommands();
    } catch (e) {
        console.error("Failed to reorder commands", e);
    }
};
// Context
const injectedContext = ref<Record<string, any>>({});
const BUSINESS_CONTEXT_RESERVED_KEYS = new Set([
  "user_id",
  "user_name",
  "username",
  "real_name",
  "user_role",
  "role",
  "role_name",
  "is_admin",
  "account_id",
  "department",
  "dept_name",
  "dept_code",
  "org_path",
  "user_dimensions",
  "user_info",
  "user",
  "current_user",
  "authenticated_user",
  "auth_user",
  "permissions",
  "permission",
  "permission_ids",
  "tenant_id",
  "tenant",
  "tenant_name",
  "org_id",
  "organization_id",
  "organization",
  "is_superuser",
  "is_staff",
  "auth_id",
  "auth_user_id",
  "api_key",
  "token",
  "access_token",
  "authorization",
]);
const BUSINESS_CONTEXT_RUNTIME_KEYS = new Set(["device_type", "display_hint"]);
const isContextRecord = (value: unknown): value is Record<string, any> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);
const sanitizeBusinessValue = (value: any): any => {
  if (Array.isArray(value)) return value.map(sanitizeBusinessValue);
  if (!isContextRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !BUSINESS_CONTEXT_RESERVED_KEYS.has(key.trim().toLowerCase()))
      .map(([key, item]) => [key, sanitizeBusinessValue(item)]),
  );
};
const mergeBusinessContext = (rawContext: unknown) => {
  if (!isContextRecord(rawContext)) return;
  const nestedContext = isContextRecord(rawContext.business_context)
    ? rawContext.business_context
    : {};
  const directContext = Object.fromEntries(
    Object.entries(rawContext).filter(
      ([key]) => key !== "business_context" && !BUSINESS_CONTEXT_RUNTIME_KEYS.has(key),
    ),
  );
  const nextBusinessContext = {
    ...(isContextRecord(injectedContext.value.business_context)
      ? injectedContext.value.business_context
      : {}),
    ...nestedContext,
    ...directContext,
  };
  const sanitizedBusinessContext = sanitizeBusinessValue(nextBusinessContext);
  const nextContext = Object.fromEntries(
    Object.entries(injectedContext.value).filter(
      ([key]) => key !== "business_context" && !BUSINESS_CONTEXT_RESERVED_KEYS.has(key),
    ),
  );
  for (const key of BUSINESS_CONTEXT_RUNTIME_KEYS) {
    if (Object.prototype.hasOwnProperty.call(rawContext, key)) {
      nextContext[key] = rawContext[key];
    }
  }
  nextContext.business_context = sanitizedBusinessContext;
  injectedContext.value = nextContext;
};
// Network
const connectionStatus = ref<"connected" | "disconnected" | "reconnecting">(
  "connected"
);
let abortController: AbortController | null = null;
let thoughtTimer: any = null;
let stallTimer: any = null;
let stalePendingTimer: ReturnType<typeof setInterval> | null = null;
const showStalledPrompt = ref(false);
const clearStalePendingTimer = () => {
  if (stalePendingTimer) {
    clearInterval(stalePendingTimer);
    stalePendingTimer = null;
  }
};
const startStalePendingTimer = (msg: Message) => {
  clearStalePendingTimer();
  stalePendingTimer = setInterval(() => {
    if (!isProcessing.value) {
      clearStalePendingTimer();
      return;
    }
    if (markStalePendingStreamLogs(msg)) {
      msg.isThinking = false;
    }
  }, 10_000);
};
const startThoughtTimer = (msg: Message) => {
  if (thoughtTimer) clearInterval(thoughtTimer);
  msg.thoughtStartTime = Date.now();
  msg.thoughtDuration = "0.0";
  let ticks = 0;
  const THINKING_MESSAGES = [
    "正在连接服务…",
    "正在分析任务…",
    "正在组织回答…",
  ];
  msg.thinkingText = THINKING_MESSAGES[0];
  thoughtTimer = setInterval(() => {
    ticks++;
    if (msg.thoughtStartTime) {
      msg.thoughtDuration = (
        (Date.now() - msg.thoughtStartTime) /
        1000
      ).toFixed(1);
      triggerRef(messages);
    }
    // Switch message every 3 seconds (30 * 100ms)
    if (ticks % 30 === 0) {
      const activePendingLog = msg.logs ? [...msg.logs].reverse().find((l) => l.status === "pending" && l.title) : null;
      if (activePendingLog && activePendingLog.title) {
        const titleStr = activePendingLog.title.startsWith("正在") ? activePendingLog.title : `正在${activePendingLog.title}`;
        msg.thinkingText = `${titleStr}…`;
      } else {
        const stepIndex = ticks / 30;
        if (stepIndex < THINKING_MESSAGES.length) {
          msg.thinkingText = THINKING_MESSAGES[stepIndex];
        } else {
          msg.thinkingText = "任务处理中，请稍候…";
        }
      }
      triggerRef(messages);
    }
  }, 100);
};
const clearStallTimer = () => {
  if (stallTimer) {
    clearTimeout(stallTimer);
    stallTimer = null;
  }
};
const resetStallTimer = () => {
  clearStallTimer();
  showStalledPrompt.value = false;
  if (isProcessing.value) {
    stallTimer = setTimeout(() => {
      showStalledPrompt.value = true;
      nextTick(() => {
        scrollToBottom();
      });
    }, 2000);
  }
};
// Slash Commands
const SYSTEM_SLASH_COMMANDS = [
  { id: "sys_clear", command: "/new", label: "新会话", sort_order: -40 },
  { id: "sys_project", command: "/project", label: "新建项目会话", sort_order: -39.5 },
  { id: "sys_history", command: "/history", label: "历史", sort_order: -39 },
  { id: DATASET_PORTAL_SYSTEM_COMMAND_ID, command: DATASET_PORTAL_SLASH_COMMAND, label: "数据门户", sort_order: -35 },
  { id: KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID, command: KNOWLEDGE_PORTAL_SLASH_COMMAND, label: "知识库中心", sort_order: -34.5 },
  { id: WORKSPACE_SYSTEM_COMMAND_ID, command: WORKSPACE_SLASH_COMMAND, label: "工作空间", sort_order: -34 },
  { id: MY_ARTIFACTS_SYSTEM_COMMAND_ID, command: MY_ARTIFACTS_SLASH_COMMAND, label: "我的产出", sort_order: -33.5 },
  { id: "sys_quota", command: "/quota", label: "我的额度", sort_order: -18 },
  { id: "sys_compact", command: "/compact", label: "压缩上下文", sort_order: -17 },
  { id: "sys_settings", command: "/settings", label: "设置", sort_order: -15 },
];
const showCommandMenu = ref(false);
const isKnowledgeEnabled = ref(true);
const slashCommands = ref<any[]>([...SYSTEM_SLASH_COMMANDS]);
// History Sidebar State
const showHistorySidebar = ref(false);
const historyList = ref<any[]>([]);
const historyPage = ref(1);
const historyHasMore = ref(true);
const loadingHistory = ref(false);
const loadingMoreHistory = ref(false);
const historyKeyword = ref("");

// --- Aggregated History Logic ---
const aggregatedHistoryList = computed(() => {
  if (!historyList.value.length) return [];

  const groups: Record<string, any> = {};
  const orderedKeys: string[] = [];

  historyList.value.forEach(item => {
    // 从根源上直接拦截并忽略没有 conversation_id 的旧垃圾测试脏数据，防止显示僵尸条目
    if (!item.conversation_id) return;

    const cid = item.conversation_id;
    if (!groups[cid]) {
      groups[cid] = item;
      orderedKeys.push(cid);
    }
  });

  return orderedKeys.map(key => groups[key]);
});

const groupedHistoryList = computed(() => groupChatHistoryByDate(aggregatedHistoryList.value));

const copiedId = ref("");
const copyToClipboard = async (text: string, id?: string) => {
  if (!text) return;
  const ok = await copyTextSecure(text);
  if (!ok) {
    console.error('Failed to copy');
    return;
  }
  if (id) {
    copiedId.value = id;
    setTimeout(() => {
      if (copiedId.value === id) copiedId.value = "";
    }, 2000);
  }
};
const fetchHistory = async (isLoadMore = false) => {
  if (!config.token && !hasPermission.value) return;

  if (isLoadMore) {
    if (!historyHasMore.value || loadingMoreHistory.value || loadingHistory.value) return;
    loadingMoreHistory.value = true;
  } else {
    loadingHistory.value = true;
    historyPage.value = 1;
    historyHasMore.value = true;
  }

  try {
    const params: any = {
      page: historyPage.value,
      page_size: 20,
      group_by_conversation: true
    };
    if (historyKeyword.value) params.keyword = historyKeyword.value;
    if (config.agentId) params.agent_id = config.agentId;

    const res = await axios.get("/api/v1/chat/history", { params });
    if (res.data?.data) {
        const newItems = res.data.data.items || [];
        if (isLoadMore) {
            historyList.value = [...historyList.value, ...newItems];
        } else {
            historyList.value = newItems;
        }

        historyHasMore.value = newItems.length >= 20;
        if (newItems.length > 0) {
            historyPage.value += 1;
        }
    }
  } catch (e) {
    console.error("Failed to fetch history", e);
  } finally {
    if (isLoadMore) {
        loadingMoreHistory.value = false;
    } else {
        loadingHistory.value = false;
    }
  }
};
const handleHistoryClick = (item: any) => {
    if (!item.conversation_id) {
        if (item.query) userInput.value = item.query;
        return;
    }

    const previousId = conversationId.value;
    if (previousId && previousId !== item.conversation_id) {
        finalizeConversationInBackground(previousId);
    }

    // Switch to this conversation
    resetEmbedThinkingOverrides();
    conversationId.value = item.conversation_id;
    persistConversationId(item.conversation_id);
    updateActiveConversationOnServer(item.conversation_id);

    // Reset message list and history state
    messages.value = [];
    historyOffset.value = 0;
    hasMoreHistory.value = true;

    // Load the full history for this conversation
    fetchConversationHistory(false);

    // Auto-close sidebar on mobile
    if (isMobile.value) {
        showHistorySidebar.value = false;
    }
};
const handleDeleteHistory = async (traceId: string) => {
  try {
    await axios.delete(`/api/v1/chat/history/${traceId}`);
    await fetchHistory();
  } catch (e) {
    console.error("Failed to delete history", e);
  }
};
const showDeleteGroupModal = ref(false);
const groupToDelete = ref<any>(null);

const handleDeleteGroup = (group: any) => {
  if (!group || !group.items || group.items.length === 0) return;
  groupToDelete.value = group;
  showDeleteGroupModal.value = true;
};

const confirmDeleteGroup = async () => {
  const group = groupToDelete.value;
  if (!group || !group.items || group.items.length === 0) {
    showDeleteGroupModal.value = false;
    groupToDelete.value = null;
    return;
  }

  const convIds = group.items
    .map((item: any) => item.conversation_id)
    .filter(Boolean);

  if (convIds.length === 0) {
    showDeleteGroupModal.value = false;
    groupToDelete.value = null;
    return;
  }

  try {
    const headers: any = {};
    if (config.token) {
      headers["Authorization"] = `Bearer ${config.token}`;
      headers["X-API-Key"] = config.token;
    }

    await axios.post(
      "/api/v1/chat/history/batch-delete",
      { conversation_ids: convIds },
      { headers }
    );

    // 检查是否包含当前正在对话的会话 ID
    if (convIds.includes(conversationId.value)) {
      messages.value = [];
      generateNewConversation();
    }

    await fetchHistory();
  } catch (e) {
    console.error("Failed to batch delete group history", e);
    alert("批量删除失败，请稍后重试");
  } finally {
    showDeleteGroupModal.value = false;
    groupToDelete.value = null;
  }
};
// Delete Confirmation
const showDeleteModal = ref(false);
const traceToDelete = ref<string | null>(null);
// Edit & Resend State
const editingMsgId = ref<number | null>(null);
const editContent = ref("");

const truncateServerHistory = async (keepCount: number): Promise<boolean> => {
  if (!conversationId.value) return true;
  try {
    const response = await axios.post(
      "/api/v1/chat/history/truncate",
      { conversation_id: conversationId.value, keep_count: keepCount },
      { headers: embedAuthHeaders() },
    );
    return response.data?.data?.success !== false;
  } catch (e) {
    console.warn("Failed to truncate server history before resend", e);
    showToast("同步会话历史失败，请稍后重试", "error");
    return false;
  }
};

const startEdit = (msg: Message) => {
  editingMsgId.value = msg.id;
  editContent.value = splitUserMessageContent(msg.content).userPart;
};
const cancelEdit = () => {
  editingMsgId.value = null;
  editContent.value = "";
};
const saveAndResend = async () => {
  await sendPreparedMessage(async () => {
    if (editingMsgId.value === null) return null;
    const msgIndex = messages.value.findIndex(m => m.id === editingMsgId.value);
    if (msgIndex === -1) return null;
    const originalMsg = messages.value[msgIndex];
    if (!originalMsg) return null;
    const newContent = editContent.value.trim();
    if (!newContent) return null;

    // 在任何 await 前冻结本次重发的参数，避免编辑框/附件被后续交互改写。
    const clientRequestId = createClientRequestId();
    const originalFiles = (originalMsg.files || []).map((file) => ({ ...file }));
    const remainingMessages = messages.value.slice(0, msgIndex);
    const keepCount = remainingMessages.filter(isChatContextMessage).length;

    if (!(await truncateServerHistory(keepCount))) return null;

    messages.value = remainingMessages;
    editingMsgId.value = null;
    editContent.value = "";
    return { content: newContent, files: originalFiles, clientRequestId };
  });
};

const handleAnalyzeDiff = async (question: string) => {
  canvasVisible.value = false;
  await sendMessage({ content: question });
};

const handleAnalyzeCodeOutput = async (question: string) => {
  canvasVisible.value = false;
  await sendMessage({ content: question });
};

const handlePreviewImageUrl = (url: string, filename: string) => {
  handleOpenCanvas({
    type: 'image',
    title: filename || '图片预览',
    content: url
  });
};

const resolveFileUrl = (rawUrl: string): string => {
  if (!rawUrl) return '';
  let url = rawUrl;
  if (url.includes('###HTML_TAG_PLACEHOLDER_')) {
    url = url.replace(/###HTML_TAG_PLACEHOLDER_\d+###/g, '').trim();
  }
  if (isDirectRenderableUrl(url)) {
    return url;
  }
  const publicUploadUrl = resolvePublicUploadsPreviewUrl(url);
  if (publicUploadUrl) return publicUploadUrl;
  // 兼容绝对路径与相对物理路径，只要它不属于静态路由与API接口路由，均通过后端预览API拉取
  if (!url.startsWith('/static/') &&
      !url.startsWith('/api/') &&
      !url.startsWith('/assets/')) {
    const convParam = conversationId.value ? `&conversation_id=${encodeURIComponent(conversationId.value)}` : "";
    return `/api/v1/chat/fs/preview?path=${encodeURIComponent(url)}${convParam}`;
  }
  return url;
};

const {
  canvasVisible,
  canvasPinned,
  canvasFromWorkspace,
  canvasData,
  handleWorkspaceFilePreview,
  handleOpenCanvas,
  closeCanvas,
  revokeActiveBlobUrl,
} = useWorkspaceCanvas({
  getConversationId: () => conversationId.value,
  resolveFileUrl,
  showToast,
  isMobile: () => isMobile.value,
});
onUnmounted(() => revokeActiveBlobUrl());

// Long-Term Memory States
const activeLtmPreference = ref<any>(null);
const ignoreLtmThisTurn = ref(false);
const ltmAlertedInSession = ref(false);

watch(conversationId, () => {
  ltmAlertedInSession.value = false;
});

const handleIgnoreLtm = () => {
  ignoreLtmThisTurn.value = true;
  activeLtmPreference.value = null;
  showToast("已在此会话本轮提问中忽略该记忆偏好", "info");
};

const canPreviewFile = (file: any) => {
  const ext = (file.ext || '').toLowerCase();
  return ext === 'pdf' || ext === 'csv' || ext === 'jpg' || ext === 'jpeg' || ext === 'png' || ext === 'webp' || ext === 'gif';
};

const handlePreviewFile = (file: any) => {
  const ext = (file.ext || '').toLowerCase();
  handleOpenCanvas({
    type: ext === 'pdf' ? 'pdf' : (ext === 'csv' ? 'csv' : 'image'),
    title: file.filename,
    content: file.url
  });
};

const confirmDeleteTrace = async () => {
  if (traceToDelete.value) {
    await handleDeleteHistory(traceToDelete.value);
    showDeleteModal.value = false;
    showTraceModal.value = false;
    traceToDelete.value = null;
  }
};
const openDeleteModal = (traceId: string) => {
    traceToDelete.value = traceId;
    showDeleteModal.value = true;
};
const formatDate = (dateStr: string) => {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
};
// Trace Modal
const showTraceModal = ref(false);
const traceLogData = ref<any>(null);
const activeHistoryItem = ref<any>(null);
const activeHistoryIndex = computed(() => {
    if (!activeHistoryItem.value || !activeHistoryItem.value.trace_id) return -1;
    return aggregatedHistoryList.value.findIndex((h: any) => h.trace_id === activeHistoryItem.value.trace_id);
});
const conversationTurns = ref<any[]>([]); // 新增：存储会话的多个回合
const loadingTrace = ref(false);
const expandedTraceSteps = ref<Record<string, boolean>>({});
const showThinkingProcess = ref(false); // Default collapsed

const openTraceLogs = async (traceIdOrItem: string | any) => {
  const isString = typeof traceIdOrItem === 'string';
  const traceId = isString ? traceIdOrItem : traceIdOrItem?.trace_id;
  let convId = isString ? null : traceIdOrItem?.conversation_id;

  if (isString) {
      const found = historyList.value.find(h => h.trace_id === traceId);
      if (found) {
          activeHistoryItem.value = found;
          convId = found.conversation_id;
      } else {
          activeHistoryItem.value = null;
      }
  } else {
      activeHistoryItem.value = traceIdOrItem;
  }

  if (!traceId) return;

  showTraceModal.value = true;
  loadingTrace.value = true;
  traceLogData.value = null;
  conversationTurns.value = [];
  expandedTraceSteps.value = {};
  showThinkingProcess.value = false;

  try {
    // 1. 先获取基础信息，特别是如果是从 trace_id 进来的，需要拿到它的 convId
    const res = await axios.get(`/api/v1/chat/logs/${traceId}`);
    if (res.data?.data) {
        traceLogData.value = res.data.data;
    }

    // 2. 获取整个会话的所有回合
    const cid = convId || traceLogData.value?.history?.conversation_id;
    if (cid) {
        const historyRes = await axios.get(`/api/v1/chat/history`, {
            params: { conversation_id: cid, page_size: 100 }
        });
        if (historyRes.data?.data?.items) {
            // 结果按时间正序排列（后端返回的是倒序，所以这里反转一下）
            const sortedItems = [...historyRes.data.data.items].reverse();

            // 初始化每个回合的状态：全部默认折叠
            conversationTurns.value = sortedItems.map((item: any) => ({
                ...item,
                steps: item.trace_id === traceId ? (traceLogData.value?.steps || []) : [],
                loading: false,
                isExpanded: false
            }));
        }
    } else if (traceLogData.value?.history) {
        conversationTurns.value = [{
            ...traceLogData.value.history,
            steps: traceLogData.value.steps || [],
            isExpanded: true
        }];
    }
  } catch (e) {
    console.error("Failed to load trace logs", e);
  } finally {
    loadingTrace.value = false;
  }
};

const toggleTurnSteps = async (turn: any) => {
    turn.isExpanded = !turn.isExpanded;
    // 如果展开且没有数据，则去加载
    if (turn.isExpanded && (!turn.steps || turn.steps.length === 0)) {
        turn.loading = true;
        try {
            const res = await axios.get(`/api/v1/chat/logs/${turn.trace_id}`);
            if (res.data?.data?.steps) {
                turn.steps = res.data.data.steps;
            }
        } catch (e) {
            console.error("Failed to fetch steps for turn", e);
        } finally {
            turn.loading = false;
        }
    }
};

const continueChatFromTrace = () => {
    const itemToLoad = activeHistoryItem.value || traceLogData.value?.history;
    if (itemToLoad) {
        handleHistoryClick(itemToLoad);
        showTraceModal.value = false;
    }
};

// Fix for mobile ghost clicks
const closeTraceModal = () => {
    // Small delay to let the click event finish on the modal before it disappears,
    // preventing it from falling through to the history sidebar backdrop.
    setTimeout(() => {
        showTraceModal.value = false;
    }, 100);
};

const handleImageUpload = () => {
  alert("多模态图片上传功能开发中...");
};
const editCommand = (cmd: any) => {
    alert(`编辑指令 [${cmd.label}] 功能开发中...`);
};
// Command Deletion State
const showDeleteCommandModal = ref(false);
const commandToDelete = ref<any>(null);
const confirmDeleteCommand = (cmd: any) => {
  commandToDelete.value = cmd;
  showDeleteCommandModal.value = true;
};
const executeDeleteCommand = async () => {
  if (!commandToDelete.value) return;
  try {
    await axios.delete(`/api/portal/slash-commands/${commandToDelete.value.id}`);
    await fetchSlashCommands();
    showDeleteCommandModal.value = false;
    commandToDelete.value = null;
  } catch (e) {
    console.error("Failed to delete command", e);
  }
};

watch(showHistorySidebar, (val) => {
    if (val && historyList.value.length === 0) {
        fetchHistory();
    }
});
watch(historyKeyword, () => {
    if (showHistorySidebar.value) {
        fetchHistory();
    }
});
// Settings
const showSettings = ref(false);
const showHelpModal = ref(false);


// 固化报表暂存状态
const showSaveReportModal = ref(false);
const isEditingReport = ref(false);
const editingReportId = ref<string | null>(null);
const saveReportForm = ref({
  id: null as number | string | null,
  title: '',
  description: '',
  sql_content: '',
  dataset_id: null as number | null,
  dataset_name: '',
  data_source: 'default_clickhouse',
  original_query: '',
  mode: 'static_sql',
  sql_template: '',
  params_schema: [] as any[],
  default_params: {} as Record<string, any>,
  column_meta: null as Record<string, any> | null,
  analysis_mode: 'auto',
  tags_input: '',
});

const showReportRunModal = ref(false);
const pendingSavedReport = ref<SavedReportPayload | null>(null);
const isPreviewingSavedReport = ref(false);
const reportRunPreview = ref<any | null>(null);
const reportRunForm = ref({
  dateRange: 'month_start_to_today',
  startDate: '',
  endDate: '',
  monthRange: 'last_6_completed_months',
  startMonth: '',
  endMonth: '',
  customParams: {} as Record<string, any>,
  autoAnalyze: true,
});

const openSaveReportModal = (sql: string, agentMessage: any) => {
  isEditingReport.value = false;
  editingReportId.value = null;
  let originalQuery = '';
  if (agentMessage && messages.value) {
    const idx = messages.value.findIndex((m: any) => m.id === agentMessage.id);
    if (idx > 0) {
      for (let i = idx - 1; i >= 0; i--) {
        const previousMessage = messages.value[i];
        if (previousMessage?.role === 'user') {
          const content = previousMessage.content || '';
          if (content.includes('---')) {
            originalQuery = (content.split('---')[0] || '').trim();
          } else {
            originalQuery = content.trim();
          }
          break;
        }
      }
    }
  }

  let cleanSql = sql || '';
  if (cleanSql.includes('[Executed SQL]:')) {
    cleanSql = cleanSql.replace(/\[Executed\s+SQL\]:\s*/i, '').trim();
  }

  if (!originalQuery && cleanSql) {
    const fromMatch = cleanSql.match(/from\s+([a-zA-Z0-9_]+)/i);
    if (fromMatch && fromMatch[1]) {
      originalQuery = `${fromMatch[1]}数据查询`;
    }
  }

  const detectedTemplate = detectSavedReportDateTemplate(cleanSql);
  const requirementIntent = parseRequirementAnalysisFromMessage(agentMessage);
  const sourceContext = resolveSavedReportSourceContext(agentMessage);

  saveReportForm.value = {
    id: null,
    title: deriveSavedReportTitle(requirementIntent, originalQuery),
    description: deriveSavedReportDescription(requirementIntent, originalQuery),
    sql_content: cleanSql,
    dataset_id: sourceContext.dataset_id,
    dataset_name: sourceContext.dataset_name,
    data_source: sourceContext.data_source,
    original_query: originalQuery,
    mode: detectedTemplate ? 'param_sql' : 'static_sql',
    sql_template: detectedTemplate?.sql_template || '',
    params_schema: detectedTemplate?.params_schema || [],
    default_params: detectedTemplate?.default_params || {},
    column_meta: extractColumnMetaFromAgentMessage(agentMessage),
    analysis_mode: 'auto',
    tags_input: deriveSavedReportTagsInput(requirementIntent, originalQuery),
  };
  showSaveReportModal.value = true;
};

const openEditReportModal = (report: any) => {
  isEditingReport.value = true;
  editingReportId.value = report.id;
  saveReportForm.value = {
    id: report.id,
    title: report.title || '',
    description: report.description || '',
    sql_content: report.sql_content || '',
    dataset_id: report.dataset_id ?? null,
    dataset_name: report.dataset_name || '',
    data_source: report.data_source || 'default_clickhouse',
    original_query: report.original_query || '',
    mode: report.mode || 'static_sql',
    sql_template: report.sql_template || '',
    params_schema: report.params_schema || [],
    default_params: report.default_params || {},
    column_meta: report.column_meta || null,
    analysis_mode: 'auto',
    tags_input: Array.isArray(report.tags) ? report.tags.join(', ') : '',
  };
  showSaveReportModal.value = true;
};

const closeSavedReportEditor = () => {
  showSaveReportModal.value = false;
  isEditingReport.value = false;
  editingReportId.value = null;
};

const handleSavedReportEditorCreated = () => {
  closeSavedReportEditor();
};

const savedReportNeedsRunOptions = (report: SavedReportPayload) => {
  return report.mode === 'param_sql' && Array.isArray(report.params_schema) && report.params_schema.length > 0;
};

const savedReportUsesMonthRange = (report?: SavedReportPayload | null) => {
  return Boolean(report?.params_schema?.some((item: any) => item?.type === 'month_range' || item?.name === 'month_range'));
};

const savedReportUsesDateRange = (report?: SavedReportPayload | null) => {
  return Boolean(report?.params_schema?.some((item: any) => item?.type === 'date_range' || item?.name === 'date_range'));
};

let suppressSavedReportRunPreviewWatch = false;

const prepareSavedReportRunForm = (report: SavedReportPayload) => {
  suppressSavedReportRunPreviewWatch = true;
  const defaults = report.default_params || {};
  const customParams: Record<string, any> = {};
  for (const item of report.params_schema || []) {
    const type = String(item?.type || '');
    const name = String(item?.name || '');
    if (!name || type === 'date_range' || type === 'month_range' || name === 'date_range' || name === 'month_range') continue;
    customParams[name] = defaults[name] ?? item.default ?? (type === 'select' ? item.options?.[0] ?? '' : '');
  }
  reportRunForm.value = {
    dateRange: String(defaults.date_range || 'month_start_to_today'),
    startDate: String(defaults.start_date || todayDateString()),
    endDate: String(defaults.end_date || todayDateString()),
    monthRange: String(defaults.month_range || 'last_6_completed_months'),
    startMonth: String(defaults.start_month || todayMonthString()),
    endMonth: String(defaults.end_month || todayMonthString()),
    customParams,
    autoAnalyze: true,
  };
  nextTick(() => {
    suppressSavedReportRunPreviewWatch = false;
  });
};

let savedReportPreviewSeq = 0;
let savedReportPreviewAbort: AbortController | null = null;

const previewSavedReportRun = async () => {
  const report = pendingSavedReport.value;
  if (!report) return;
  const seq = ++savedReportPreviewSeq;
  savedReportPreviewAbort?.abort();
  const controller = new AbortController();
  savedReportPreviewAbort = controller;
  isPreviewingSavedReport.value = true;
  reportRunPreview.value = null;
  try {
    const res = await axios.post(`/api/portal/saved-reports/${report.id}/preview`, {
      params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value),
      analysis_mode: 'auto',
    }, { signal: controller.signal });
    if (seq !== savedReportPreviewSeq) return;
    reportRunPreview.value = res.data?.data || null;
  } catch (error: any) {
    if (controller.signal.aborted || seq !== savedReportPreviewSeq) return;
    console.error("Failed to preview saved report:", error);
    reportRunPreview.value = {
      rendered_sql: report.sql_content,
      permission_status: 'unknown',
      permission_message: extractSavedReportExecuteErrorMessage(error),
      can_run: true,
    };
  } finally {
    if (seq === savedReportPreviewSeq) {
      isPreviewingSavedReport.value = false;
    }
  }
};

let savedReportPreviewTimer: ReturnType<typeof setTimeout> | null = null;

const scheduleSavedReportPreview = (immediate = false) => {
  if (!showReportRunModal.value || !pendingSavedReport.value) return;
  if (!immediate && suppressSavedReportRunPreviewWatch) return;
  if (savedReportPreviewTimer) clearTimeout(savedReportPreviewTimer);
  if (immediate) {
    void previewSavedReportRun();
    return;
  }
  savedReportPreviewTimer = setTimeout(() => previewSavedReportRun(), 250);
};

watch(
  () => [
    reportRunForm.value.dateRange,
    reportRunForm.value.startDate,
    reportRunForm.value.endDate,
    reportRunForm.value.monthRange,
    reportRunForm.value.startMonth,
    reportRunForm.value.endMonth,
    JSON.stringify(reportRunForm.value.customParams),
  ],
  () => scheduleSavedReportPreview(false),
  { flush: 'post' }
);

onUnmounted(() => {
  savedReportPreviewAbort?.abort();
  if (savedReportPreviewTimer) clearTimeout(savedReportPreviewTimer);
});

const handleExecuteSavedReport = async (report: SavedReportPayload) => {
  if (!savedReportNeedsRunOptions(report)) {
    pendingSavedReport.value = report;
    reportRunPreview.value = null;
    await executeSavedReportWithOptions(report);
    return;
  }
  pendingSavedReport.value = report;
  reportRunPreview.value = null;
  showReportRunModal.value = true;
  prepareSavedReportRunForm(report);
  scheduleSavedReportPreview(true);
};

const executeSavedReportWithOptions = async (reportArg?: SavedReportPayload | null) => {
  const report = reportArg || pendingSavedReport.value;
  if (!report) return;
  if (isProcessing.value) return;
  if (savedReportNeedsRunOptions(report) && (isPreviewingSavedReport.value || !reportRunPreview.value)) {
    showToast('请等待运行预览完成后再执行。', 'error');
    return;
  }
  if (reportRunPreview.value?.can_run === false) {
    showToast('暂无该报表所需数据权限，无法运行。', 'error');
    return;
  }

  showReportRunModal.value = false;

  if (showPortalDrawer.value && !portalKeepOpenOnQuestion.value) {
    closePortalDrawer();
  }

  // 保证有会话 ID，否则 Redis last_data_result 无法写入，后续「可视化分析」会丢上下文
  if (!conversationId.value) {
    generateNewConversation();
  }

  isProcessing.value = true;

  messages.value.push({
    id: Date.now(),
    role: "user",
    content: `📌 执行固化 SQL 报表: ${report.title}`,
    timestamp: new Date().toISOString(),
  });

  const agentMsg = ref<Message>({
    id: Date.now() + 1,
    role: "agent",
    agentName: "chat-bi",
    agentDisplayName: "数据智能助手",
    isSavedReportResult: true,
    content: "",
    isThinking: true,
    thinkingText: "正在执行固化报表，请稍候...",
    logs: [],
    thoughtStartTime: Date.now(),
    thoughtDuration: "0.0",
    isThoughtExpanded: false,
    isCitationsExpanded: false,
    timestamp: new Date().toISOString(),
  });
  messages.value.push(agentMsg.value);
  autoScrollEnabled.value = true;
  await nextTick();
  scrollToBottom(true);

  try {
    const res = await axios.post(`/api/portal/saved-reports/${report.id}/execute`, {
      params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value),
      analysis_mode: 'auto',
      defer_analysis: true,
    }, {
      params: { conversation_id: conversationId.value },
      timeout: 60000,
    });

    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";

    let detailsText = "";
    let execResult: any = null;

    if (res.data && res.data.data !== undefined) {
      execResult = {
        ...res.data.data,
        analysis_status: res.data.data?.analysis_status || 'deferred',
      };
      agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, execResult);
      detailsText = `${report.sql_content}\n--- 结果 ---\n${typeof execResult === 'object' ? JSON.stringify(execResult, null, 2) : String(execResult)}`;
      agentMsg.value.permissionNotice = execResult?.permission_notice;
      if (execResult?.chatbi_insight?.actions?.length) {
        agentMsg.value.chatbiInsight = execResult.chatbi_insight;
      }
    } else {
      agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, null);
      detailsText = `${report.sql_content}\n--- 结果 ---\n无`;
    }

    agentMsg.value.logs = [
      {
        id: `log_${Date.now()}`,
        name: "execute_sql_query",
        title: "工具完成: execute_sql_query",
        category: "sql",
        status: "success",
        isExpanded: false,
        details: detailsText,
      }
    ];
    pendingSavedReport.value = null;

    if (execResult) {
      try {
        agentMsg.value.isThinking = true;
        agentMsg.value.thinkingText = "正在生成业务解读…";
        const analyzeRes = await axios.post(`/api/portal/saved-reports/${report.id}/analyze`, {
          conversation_id: conversationId.value,
          run_id: execResult.run_id,
        }, {
          params: { conversation_id: conversationId.value },
          timeout: 120000,
        });
        const merged = mergeSavedReportAnalysisIntoResult(execResult, analyzeRes.data?.data || {});
        agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, merged);
      } catch (analyzeError) {
        console.warn("Failed to analyze saved report:", analyzeError);
        agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, {
          ...execResult,
          analysis_status: 'error',
        });
      } finally {
        agentMsg.value.isThinking = false;
        agentMsg.value.thinkingText = "";
      }
    }
  } catch (error: any) {
    console.error("Failed to execute saved report:", error);
    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";

    const errorMsg = extractSavedReportExecuteErrorMessage(error);

    agentMsg.value.content = `### ❌ 报表执行失败\n\n在直连执行 SQL 报表时遇到错误：\n\n\`\`\`\n${errorMsg}\n\`\`\``;
    agentMsg.value.logs = [
      {
        id: `log_${Date.now()}`,
        name: "execute_sql_query",
        title: "工具完成: execute_sql_query",
        category: "sql",
        status: "error",
        isExpanded: false,
        details: `${report.sql_content}\n--- 错误 ---\n${errorMsg}`,
      }
    ];
  } finally {
    isProcessing.value = false;
    await nextTick();
    scrollToBottom(true);
  }
};

watch(showSettings, (val) => {
    if (val) {
        fetchAllowedAgents();
    }
});
const activeColor = ref("#1677ff");

// 技能工作流选择器
const skillCreatedInfo = ref<SkillCreatedInfo | null>(null);

watch(
  () =>
    messages.value
      .map((m) =>
        [
          m.content || "",
          ...((m.logs || []).map((log: any) => String(log.details || ""))),
        ].join("\n"),
      )
      .join("\n---\n"),
  (blob) => {
    const info = parseSkillCreatedMarker(blob);
    if (info && info.scope === "personal") {
      skillCreatedInfo.value = info;
    }
  },
);

const mountCreatedSkill = () => {
  if (!skillCreatedInfo.value) return;
  handleSelectSkill({
    id: skillCreatedInfo.value.skill_id,
    name: skillCreatedInfo.value.name,
    description: "",
    scope: skillCreatedInfo.value.scope,
  });
  skillCreatedInfo.value = null;
};

const handleSelectSkill = (skill: any) => {
  if (!chatInputRef.value) return;
  const scope = skill.scope === "personal" ? "personal" : "global";
  chatInputRef.value.uploadedFiles.push({
    type: "skill",
    url: skill.id,
    filename: `${skill.name} (技能)`,
    size: 0,
    ext: "skill",
    scope,
    skillMeta: {
      id: skill.id,
      name: skill.name,
      description: skill.description || "",
      scope,
    },
  });
};

const openMemorySelector = () => {
  showMemoryDrawer.value = true;
};

const handleMemoryMount = (memory: {
  conversation_id: string;
  summary: string;
  last_active?: number;
}) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const memFile = files.find((f: any) => f.type === "memory");
  const existingIds = memFile?.url
    ? String(memFile.url).split(",").map((id) => id.trim()).filter(Boolean)
    : [];
  if (existingIds.includes(memory.conversation_id)) return;
  const existingMeta = memFile?.memoryMeta || [];
  const newIds = [...existingIds, memory.conversation_id];
  const newMeta = [
    ...existingMeta,
    {
      conversation_id: memory.conversation_id,
      summary: memory.summary,
      last_active: memory.last_active,
    },
  ];
  chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
  chatInputRef.value.uploadedFiles.push({
    type: "memory",
    url: newIds.join(","),
    filename: `已选择 ${newIds.length} 条记忆记录`,
    size: 0,
    ext: "memory",
    memoryMeta: newMeta,
  });
};

const handleMemoryCleared = (payload: { conversationIds: string[]; all?: boolean }) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const memFile = files.find((f: any) => f.type === "memory");
  if (!memFile?.url) return;
  if (payload.all) {
    chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
    return;
  }
  const remainingIds = String(memFile.url)
    .split(",")
    .map((id) => id.trim())
    .filter((id) => id && !payload.conversationIds.includes(id));
  const remainingMeta = (memFile.memoryMeta || []).filter(
    (m: { conversation_id: string }) => !payload.conversationIds.includes(m.conversation_id),
  );
  chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
  if (remainingIds.length > 0) {
    chatInputRef.value.uploadedFiles.push({
      ...memFile,
      url: remainingIds.join(","),
      filename: `已选择 ${remainingIds.length} 条记忆记录`,
      memoryMeta: remainingMeta,
    });
  }
};


const availableModels = ref<AIModel[]>([]);
const currentUser = ref<any>(null);
const accountInfo = ref<any>(null); // System account info from /me
const showShortcutsHint = ref(false);
let shortcutsHintTimer: ReturnType<typeof setTimeout> | null = null;

const dismissShortcutsHint = () => {
  showShortcutsHint.value = false;
  if (shortcutsHintTimer) {
    clearTimeout(shortcutsHintTimer);
    shortcutsHintTimer = null;
  }
};

const triggerShortcutsHint = () => {
  showShortcutsHint.value = true;
  if (shortcutsHintTimer) clearTimeout(shortcutsHintTimer);
  shortcutsHintTimer = setTimeout(() => {
    showShortcutsHint.value = false;
    shortcutsHintTimer = null;
  }, 4500);
};

const toggleShortcuts = () => {
  config.showShortcuts = !config.showShortcuts;
  saveRoutingSettings();
  if (config.showShortcuts) {
    dismissShortcutsHint();
    fetchSlashCommands();
  } else {
    triggerShortcutsHint();
  }
};

const handleHeaderShortcutsClick = () => {
  dismissShortcutsHint();
  if (isMobile.value) {
    fetchSlashCommands();
    chatInputRef.value?.openCommandDrawer?.();
    return;
  }
  toggleShortcuts();
};
const isFullScreen = ref(false);
const toggleFullScreen = () => {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(err => {
      console.error(`Error attempting to enable full-screen mode: ${err.message}`);
    });
  } else {
    document.exitFullscreen();
  }
};
const updateFullScreenStatus = () => {
  isFullScreen.value = !!document.fullscreenElement;
};

const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 640);
const updateWidth = () => {
  windowWidth.value = window.innerWidth;
  // Inject device context
  injectedContext.value = {
    ...injectedContext.value,
    device_type: isMobile.value ? '移动端(小屏幕)' : '桌面端(大屏幕)',
    display_hint: isMobile.value ? '窄屏排版优化' : '宽屏详尽展示'
  };
};
const showAddModal = ref(false);
const newCommand = reactive({
  label: "",
  command: "",
  sort_order: 10,
});
const addCommand = async () => {
  if (!newCommand.label || !newCommand.command) return;
  try {
    const username = currentUser.value?.user_name || "unknown";
    await axios.post("/api/portal/slash-commands/", {
      ...newCommand,
      created_by: username
    });
    await fetchSlashCommands();
    showAddModal.value = false;
    newCommand.label = "";
    newCommand.command = "";
  } catch (e) {
    console.error("Failed to add command", e);
  }
};
// --- PostMessage Protocol ---
const postMessageToHost = (payload: any) => {
  if (config.instanceId) {
    payload.instance_id = config.instanceId;
  }
  window.parent?.postMessage(
    {
      source: "nanzi-agent-embed",
      ...payload,
    },
    "*"
  );
};
const savedReportFocusRequest = ref<{
  report_id: string;
  run_id: string;
  request_id: string;
  detail_tab?: "info" | "runs" | "subscription";
  run_now?: boolean;
} | null>(null);
let savedReportFocusSequence = 0;
const openSavedReportFromHost = (target: any) => {
  if (!target?.report_id) return;
  const requestId = String(
    target.request_id || `embed-report-${Date.now().toString(36)}-${++savedReportFocusSequence}`,
  );
  if (savedReportFocusRequest.value?.request_id === requestId) return;
  savedReportFocusRequest.value = {
    report_id: String(target.report_id),
    run_id: String(target.run_id || ""),
    request_id: requestId,
    ...(target.detail_tab ? { detail_tab: target.detail_tab } : {}),
    ...(target.run_now ? { run_now: true } : {}),
  };
  setTimeout(() => openPortalDrawer(), 0);
};
const applyInitConfigPayload = (data: Record<string, any>) => {
  void loadWorkbenchHome();
  const incomingInstanceId = normalizeEmbedInstanceId(data.instance_id);
  const instanceChanged = Boolean(incomingInstanceId) && incomingInstanceId !== config.instanceId;
  if (incomingInstanceId) config.instanceId = incomingInstanceId;
  if (instanceChanged) {
    requestedConversationId = "";
    conversationId.value = "";
    messages.value = [];
    resourceScope.value = emptyResourceScopeState();
    historyRequestSequence += 1;
    historyOffset.value = 0;
    hasMoreHistory.value = true;
    isLoadingHistory.value = false;
  }
  if (data.agent_id) {
    const agentId = String(data.agent_id);
    applyIntegrationAgentLock(agentId);
    void loadWelcomeCards(agentId);
    if (!data.conversation_id) {
      messages.value = [];
      generateNewConversation();
      requestedConversationId = conversationId.value;
    }
  }
  if (data.conversation_id) {
    requestedConversationId = String(data.conversation_id);
    conversationId.value = requestedConversationId;
    persistConversationId(requestedConversationId);
  } else if (!data.agent_id) {
    requestedConversationId = "";
  }
  if (data.theme) applyTheme(data.theme, data.styleVars);
  if (data.welcome_message_override) config.welcomeMessage = data.welcome_message_override;
  if (data.user_avatar) config.userAvatar = data.user_avatar;
  if (data.business_context) mergeBusinessContext(data.business_context);
  if (data.page_info) {
    mergeBusinessContext(data.page_info);
  }
  openSavedReportFromHost(data.open_saved_report);
  if (data.portal_question?.query) {
    setTimeout(
      () =>
        handlePortalQuickQuestion(
          String(data.portal_question.query),
          data.portal_question.action === "fill" ? "fill" : "send",
        ),
      0,
    );
  }
};

const exchangeTicketAndApply = async (ticket: string): Promise<boolean> => {
  try {
    const res = await axios.post("/api/v1/embed/tickets/exchange", { ticket: ticket.trim() });
    if (res.data && res.data.code === 200 && res.data.data?.session_token) {
      const sessionData = res.data.data;
      config.token = sessionData.session_token;
      axios.defaults.headers.common["Authorization"] = `Bearer ${sessionData.session_token}`;
      axios.defaults.headers.common["X-API-Key"] = sessionData.session_token;
      if (sessionData.user_info) {
        currentUser.value = {
          ...currentUser.value,
          ...sessionData.user_info,
        };
      }
      if (sessionData.agent_id) {
        urlPinnedAgentKey.value = sessionData.agent_id;
        applyIntegrationAgentLock(sessionData.agent_id);
      }
      return true;
    }
    return false;
  } catch (err) {
    console.error("[EmbedTicket] Failed to exchange ticket:", err);
    return false;
  }
};

const postInitSuccess = () => {
  postMessageToHost({
    type: "INIT_SUCCESS",
    user: {
      user_name: currentUser.value?.user_name || undefined,
      real_name: currentUser.value?.real_name || undefined,
      user_id: currentUser.value?.user_id || undefined,
      role: currentUser.value?.role || undefined,
    },
  });
};

const handleInitConfig = async (data: Record<string, any>) => {
  initConfigReceived = true;
  conversationInitializationGeneration += 1;
  cancelPendingUrlTokenInitialization();
  const logData = { ...data };
  delete logData.user_info;
  delete logData.user;
  console.log(
    "Received INIT_CONFIG payload:",
    JSON.stringify({
      ...logData,
      ticket: logData.ticket ? "***" : undefined,
      token: logData.token ? "***" : "MISSING",
      api_key: logData.api_key ? "***" : "MISSING",
      apikey: logData.apikey ? "***" : "MISSING",
    }),
  );
  const strict = data.strict_token === true;
  if (strict) {
    strictTokenValidation.value = true;
  }

  // 1. 优先使用临时 Ticket 换票
  if (data.ticket) {
    const ticketOk = await exchangeTicketAndApply(String(data.ticket));
    if (!ticketOk) {
      hasPermission.value = false;
      postMessageToHost({ type: "INIT_FAILURE", reason: "invalid_ticket" });
      return;
    }
    hasPermission.value = true;
    applyInitConfigPayload(data);
    postInitSuccess();
    await initChat({ skipAuth: true });
    return;
  }

  // 2. 兼容传统的 API Key 模式
  const incomingToken = data.token || data.api_key || data.apikey;
  if (!incomingToken) {
    console.warn("INIT_CONFIG received but no token/api_key/ticket found in payload!");
    if (strict) {
      hasPermission.value = false;
      postMessageToHost({ type: "INIT_FAILURE", reason: "missing_token" });
    }
    return;
  }
  config.token = incomingToken;
  axios.defaults.headers.common["Authorization"] = `Bearer ${incomingToken}`;
  axios.defaults.headers.common["X-API-Key"] = incomingToken;

  if (strict) {
    const isValid = await validateToken({ strict: true });
    if (!isValid) {
      hasPermission.value = false;
      postMessageToHost({ type: "INIT_FAILURE", reason: "invalid_token" });
      return;
    }
    hasPermission.value = true;
    applyInitConfigPayload(data);
    postInitSuccess();
    await initChat({ skipAuth: true });
    return;
  }

  hasPermission.value = true;
  applyInitConfigPayload(data);
  postInitSuccess();
  initChat();
};
const handlePostMessage = (event: MessageEvent) => {
  // Security check logic here in production
  const data = event.data;
  const messageInstanceId = normalizeEmbedInstanceId(data.instance_id);
  if (
    messageInstanceId &&
    config.instanceId &&
    messageInstanceId !== config.instanceId
  ) {
    return; // Ignore messages for other instances
  }
  switch (data.type) {
    case "INIT_CONFIG":
      void handleInitConfig(data);
      break;
    case "OPEN_SAVED_REPORT":
      openSavedReportFromHost(data.open_saved_report);
      break;
    case "SYNC_STATE":
      // Host syncing page state (e.g. current URL, selected item)
      if (data.payload) {
        mergeBusinessContext(data.payload);
        console.log("State synced from host:", injectedContext.value.business_context);
      }
      break;
    case "UPDATE_CONTEXT":
      const newContext = data.payload || data.context;
      if (newContext) {
        mergeBusinessContext(newContext);
        console.log("Context updated:", injectedContext.value.business_context);
      }
      break;
    case "SET_THEME":
      applyTheme(data.theme, data.styleVars);
      break;
    case "STOP_GENERATION":
      stopGeneration();
      postMessageToHost({ type: "GENERATION_STOPPED" });
      break;
    case "CLEAR_SESSION":
      resetSession();
      break;
    case "RESET_SESSION":
      void resetSession(data.new_token || data.token, data.ticket);
      break;
    case "SEND_COMMAND":
      if (data.command) {
        handleQuickQuestion(data.command);
      }
      break;
  }
};
const applyTheme = (theme: string, styleVars?: Record<string, string>) => {
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  if (styleVars) {
    Object.entries(styleVars).forEach(([key, value]) => {
      root.style.setProperty(key, value);
    });
  }
};
const resetSession = async (newToken?: string, ticket?: string) => {
  messages.value = [];
  config.enableGrounding = true; // 新会话恢复默认开启
  generateNewConversation();
  if (ticket) {
    const ticketOk = await exchangeTicketAndApply(ticket);
    if (!ticketOk) {
      hasPermission.value = false;
      postMessageToHost({ type: "INIT_FAILURE", reason: "invalid_ticket" });
      return;
    }
    hasPermission.value = true;
  } else if (newToken) {
    config.token = newToken;
    axios.defaults.headers.common["Authorization"] = `Bearer ${newToken}`;
    axios.defaults.headers.common["X-API-Key"] = newToken;
  }
  initChat();
  // 通知门户父页去掉 URL 中的 conversation_id，避免再次 INIT 或刷新又钉回旧会话
  postMessageToHost({
    type: "CONVERSATION_CHANGED",
    conversation_id: conversationId.value,
    clear_host_conversation_pin: true,
  });
};
const handleConfirmClearSession = () => {
  resetSession();
  showConfirmModal.value = false;
  nextTick(() => {
    if (!isMobile.value) {
      chatInputRef.value?.focus();
    }
  });
};
const fetchSlashCommands = async () => {
  try {
    const headers: any = {};
    if (config.token) {
      headers["Authorization"] = `Bearer ${config.token}`;
      headers["X-API-Key"] = config.token;
    }
    // 并行获取 RAGFlow 配置和快捷指令
    const [configRes, res] = await Promise.all([
      axios.get("/api/portal/ragflow/config", { headers }).catch(e => {
        console.warn("Failed to fetch ragflow config", e);
        return null;
      }),
      axios.get("/api/portal/slash-commands/", { headers }).catch(e => {
        console.warn("Failed to fetch user slash-commands", e);
        return { data: null };
      })
    ]);

    if (configRes && configRes.data?.data) {
      isKnowledgeEnabled.value = configRes.data.data.knowledge_base_enabled !== false;
    } else {
      isKnowledgeEnabled.value = true;
    }

    const sysCommands = SYSTEM_SLASH_COMMANDS.map(cmd => {
      if (cmd.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID) {
        return {
          ...cmd,
          disabled: !isKnowledgeEnabled.value
        };
      }
      return cmd;
    });

    if (res.data) {
      // 获取用户命令
      const userCommands = Array.isArray(res.data) ? res.data : [];
      // 合并系统命令和用户命令，并按 sort_order 排序
      slashCommands.value = [
        ...sysCommands,
        ...userCommands
      ].sort((a, b) => (a.sort_order || 999) - (b.sort_order || 999));
    } else {
      slashCommands.value = [...sysCommands];
    }
  } catch (e) {
    console.warn("Slash commands fetch failed", e);
    const sysCommands = SYSTEM_SLASH_COMMANDS.map(cmd => {
      if (cmd.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID) {
        return {
          ...cmd,
          disabled: !isKnowledgeEnabled.value
        };
      }
      return cmd;
    });
    slashCommands.value = [...sysCommands];
  }
};
const fetchModels = async () => {
  try {
    const res = await modelApi.list();
    if (res.data) {
      availableModels.value = res.data.filter(
        (m) => (m.type === "llm" || m.type === "multimodal") && m.is_active
      );
    }
  } catch (e) {
    console.error("Failed to fetch models", e);
  }
};
const fetchAccountInfo = async () => {
  // Placeholder for future use (e.g., wallet, balance, user profile)
  return;
};
// State
const hasPermission = ref(true); // Default to true, strictly controlled by validateToken
/** 调试台 strict_token 模式：仅校验 INIT_CONFIG 传入的 token，不走 localStorage / Cookie 兜底。 */
const strictTokenValidation = ref(false);

/** 仅在服务端校验通过后写入，避免 URL 里陈旧的 ?token= 覆盖刚登录写入的 api_key（父页 Chat.vue postMessage 会读 localStorage）。 */
const syncValidatedCredentials = (apiKey: string) => {
  config.token = apiKey;
  localStorage.setItem("yovole_token", apiKey);
  localStorage.setItem("api_key", apiKey);
  axios.defaults.headers.common["Authorization"] = `Bearer ${apiKey}`;
  axios.defaults.headers.common["X-API-Key"] = apiKey;
};

const validateToken = async (options?: { strict?: boolean }): Promise<boolean> => {
  const strict = options?.strict ?? strictTokenValidation.value;
  const attachUser = (data: Record<string, unknown>) => {
    accountInfo.value = data as typeof accountInfo.value;
    currentUser.value = data as typeof currentUser.value;
  };

  const tryOnce = async (headers: Record<string, string>) => {
    const response = await axios.get("/api/portal/auth/user_apikey", { headers });
    if (response.status === 200 && response.data?.status === "success") {
      attachUser(response.data.data);
      return true;
    }
    return false;
  };

  const authHeaders = (token: string) => ({
    Authorization: `Bearer ${token}`,
    "X-API-Key": token,
  });

  if (strict) {
    const token = config.token?.trim();
    if (!token) return false;
    try {
      const ok = await tryOnce(authHeaders(token));
      if (ok) {
        config.token = token;
        axios.defaults.headers.common["Authorization"] = `Bearer ${token}`;
        axios.defaults.headers.common["X-API-Key"] = token;
        console.log("[Auth] Strict validation success:", accountInfo.value?.user_name);
        return true;
      }
    } catch (error: any) {
      const status = error.response?.status;
      console.warn("[Auth] Strict validation failed:", status || error.message);
    }
    return false;
  }

  const candidates: string[] = [];
  const add = (t?: string | null) => {
    const s = t?.trim();
    if (s && !candidates.includes(s)) candidates.push(s);
  };
  add(config.token);
  add(localStorage.getItem("api_key"));
  add(localStorage.getItem("yovole_token"));

  console.log("[Auth] Starting validation, candidates:", candidates.length);

  for (const key of candidates) {
    try {
      const ok = await tryOnce(authHeaders(key));
      if (ok) {
        syncValidatedCredentials(key);
        console.log("[Auth] Validation success:", accountInfo.value?.user_name);
        return true;
      }
    } catch (error: any) {
      const status = error.response?.status;
      if (status === 401 || status === 403) {
        console.warn("[Auth] Key candidate rejected (" + status + "), trying next...");
        continue;
      }
      console.warn("[Auth] Validation error (network/server):", error.message);
      return false;
    }
  }

  // 无有效 Header 凭据时尝试仅携带 Cookie（httponly admin_token），且不走 axios 拦截器以免带上失效的 localStorage
  try {
    const res = await fetch("/api/portal/auth/user_apikey", { credentials: "include" });
    if (res.ok) {
      const body = await res.json();
      if (body?.status === "success" && body.data) {
        attachUser(body.data);
        localStorage.removeItem("api_key");
        localStorage.removeItem("yovole_token");
        delete axios.defaults.headers.common["Authorization"];
        delete axios.defaults.headers.common["X-API-Key"];
        config.token = "";
        console.log("[Auth] Validation success via session cookie:", accountInfo.value?.user_name);
        return true;
      }
    }
  } catch (e: any) {
    console.warn("[Auth] Cookie-only validation failed:", e.message);
  }

  return false;
};
const initChat = async (options?: { skipAuth?: boolean }) => {
  const initGeneration = conversationInitializationGeneration;
  isInitialLoading.value = true;
  try {
    // 1. Validate Token First (The only blocking step)
    if (!options?.skipAuth) {
      const isValid = await validateToken();
      if (initGeneration !== conversationInitializationGeneration) return;
      if (!isValid) {
        hasPermission.value = false;
        isInitialLoading.value = false;
        return;
      }
    }
    if (initGeneration !== conversationInitializationGeneration) return;
    hasPermission.value = true;
    // 2. Clear skeleton as soon as auth is confirmed
    isInitialLoading.value = false;
    // 3. Set default welcome message if not provided
    if (!config.welcomeMessage) {
      const displayName = accountInfo.value?.real_name || accountInfo.value?.user_name || "";
      const greeting = displayName ? `您好，${displayName}！` : "您好！";
      config.welcomeMessage = `${greeting}我是你的智能体助手，很高兴为您服务。`;
    }
    // 初始页「我的资源」统计：鉴权成功后拉取一次（失败时前端走 fallback）
    void loadWorkbenchHome();
    // 4. Background tasks (non-blocking)
    Promise.all([fetchModels(), fetchAccountInfo(), fetchSlashCommands()]).catch(err => {
      console.warn("[Init] Non-critical background loading failed:", err.message);
    });
    // 5. Preload Agents & Validate Expert Mode / URL agent lock
    if (urlPinnedAgentKey.value) {
      const ok = await resolveUrlPinnedAgent();
      if (initGeneration !== conversationInitializationGeneration) return;
      if (!ok) {
        isInitialLoading.value = false;
        return;
      }
    }
    fetchAllowedAgents().then(() => {
        if (isRoutingSettingsLocked.value) {
            // 集成锁定时不写入用户偏好，避免覆盖用户自己的默认智能体。
            if (integrationAgentLockId.value) {
              applyIntegrationAgentLock(integrationAgentLockId.value);
            }
            return;
        }
        if (config.routingMode === 'expert' && config.expertAgentId) {
            const isValid = allowedAgents.value.some(a => a.id === config.expertAgentId);
            if (!isValid) {
                console.warn("[Init] Saved expert agent invalid/unauthorized. Downgrading to Auto.");
                switchToAuto();
            }
        }
    }).catch(e => console.warn("Failed to preload agents", e));
    // 6. Workbench/host explicit resume wins; otherwise fetch the active conversation.
    if (initGeneration !== conversationInitializationGeneration) return;
    let loadedCid = false;
    if (requestedConversationId) {
      conversationId.value = requestedConversationId;
      persistConversationId(requestedConversationId);
      updateActiveConversationOnServer(requestedConversationId);
      loadedCid = true;
    } else {
      const savedId = readStoredConversationId();
      if (savedId) conversationId.value = savedId;
      if (shouldUseServerActiveConversation()) {
        try {
          const activeRes = await axios.get("/api/v1/chat/active", {
            params: activeConversationRequestParams(),
            headers: embedAuthHeaders()
          });
          if (initGeneration !== conversationInitializationGeneration) return;
          if (activeRes.data?.status === "success" && activeRes.data?.data?.conversation_id) {
            conversationId.value = activeRes.data.data.conversation_id;
            persistConversationId(conversationId.value);
            loadedCid = true;
          }
        } catch (e: any) {
          console.warn("[Init] Failed to fetch active conversation from server:", e);
        }
      }
    }
    if (initGeneration !== conversationInitializationGeneration) return;

    if (!loadedCid) {
      if (!conversationId.value) {
        generateNewConversation();
      } else {
        updateActiveConversationOnServer(conversationId.value);
      }
    }

    // 7. Load history if exists
    if (conversationId.value) {
      fetchConversationHistory(false, initGeneration).catch(e => console.error("[Init] History load failed:", e));
    }
  } catch (e) {
    console.error("Init chat failed", e);
    isInitialLoading.value = false;
  }
};
// History State
const historyOffset = ref(0);
const hasMoreHistory = ref(true);
const HISTORY_LIMIT = 20;
const isLoadingHistory = ref(false);
let historyRequestSequence = 0;
const fetchConversationHistory = async (
  isLoadMore = false,
  expectedInitializationGeneration = conversationInitializationGeneration,
) => {
  if (!conversationId.value) return;
  if (isLoadMore && !hasMoreHistory.value) return;
  if (isLoadingHistory.value) return;
  const requestSequence = ++historyRequestSequence;
  const historyConversationId = conversationId.value;
  isLoadingHistory.value = true;
  // Save current scroll height to maintain position after loading
  const container = messagesContainer.value;
  const oldScrollHeight = container ? container.scrollHeight : 0;
  const oldScrollTop = container ? container.scrollTop : 0;
  try {
    const headers: any = {};
    if (config.token) {
      headers["Authorization"] = `Bearer ${config.token}`;
      headers["X-API-Key"] = config.token;
    }
    const page = Math.floor((isLoadMore ? historyOffset.value : 0) / HISTORY_LIMIT) + 1;
    const res = await axios.get(
      `/api/v1/chat/history`,
      {
          params: { conversation_id: historyConversationId, page: page, page_size: HISTORY_LIMIT },
          headers
      }
    );
    if (
      requestSequence !== historyRequestSequence ||
      expectedInitializationGeneration !== conversationInitializationGeneration ||
      conversationId.value !== historyConversationId
    ) return;
    if (res.data?.data && Array.isArray(res.data.data.items)) {
      const rawItems = res.data.data.items;
      // Update offset and check if more
      if (rawItems.length < HISTORY_LIMIT) {
        hasMoreHistory.value = false;
      }
      historyOffset.value += rawItems.length;

      const newHistoryBatch: Message[] = [];
      const offset = isLoadMore ? historyOffset.value : 0;

      // Items are returned newest first. Reverse to oldest first for UI.
      const sortedItems = [...rawItems].reverse();

      sortedItems.forEach((item: any, idx: number) => {
          if (item.query) {
              newHistoryBatch.push({
                  id: Date.now() + idx * 2 + offset,
                  trace_id: item.trace_id,
                  role: 'user',
                  content: item.query,
                  logs: [],
                  isThinking: false,
                  feedback: null,
                  timestamp: item.created_at
              });
          }
          if (item.summary || item.process_timeline || item.reasoning_content) {
              newHistoryBatch.push({
                  id: Date.now() + idx * 2 + 1 + offset,
                  trace_id: item.trace_id,
                  role: 'agent',
                  content: item.summary,
                  reasoningContent: item.reasoning_content ?? undefined,
                  processTimeline: hydrateHistoryProcessTimeline(item.process_timeline, item.reasoning_content),
                  logs: [],
                  isThinking: false,
                  feedback: null,
                  agentName: item.agent_name ?? undefined,
                  agentDisplayName: item.agent_display_name || (String(item.agent_name || '').startsWith('sys_') ? '系统助手' : undefined),
                  agentType: item.agent_type ?? undefined,
                  prompt_tokens: item.prompt_tokens ?? undefined,
                  completion_tokens: item.completion_tokens ?? undefined,
                  total_tokens: item.total_tokens ?? undefined,
                  hasDataOutput: Boolean(item.has_data_output),
                  reusableResultStatus: item.reusable_result_id
                    ? {
                        status: item.reusable_result_status || "saved",
                        resultId: String(item.reusable_result_id),
                      }
                    : undefined,
                  timestamp: item.created_at
              });
          }
      });
      if (newHistoryBatch.length > 0) {
        if (
          requestSequence !== historyRequestSequence ||
          expectedInitializationGeneration !== conversationInitializationGeneration ||
          conversationId.value !== historyConversationId
        ) return;
        if (isLoadMore) {
           // Prepend to messages (remove existing "History Start" separator if it exists)
           messages.value = [...newHistoryBatch, ...messages.value.filter(m => m.role !== 'system' || m.content !== '以上是历史会话，可以重置会话清除')];
           // Restore scroll position
           await nextTick();
           if (
             requestSequence !== historyRequestSequence ||
             expectedInitializationGeneration !== conversationInitializationGeneration ||
             conversationId.value !== historyConversationId
           ) return;
           if (container) {
             const newScrollHeight = container.scrollHeight;
             const heightAdded = newScrollHeight - oldScrollHeight;
             // Use behavior: 'instant' to prevent jumps and ignore any default scrolling behaviors
             container.scrollTo({
                top: heightAdded + oldScrollTop,
                behavior: 'instant' as any
             });
           }
        } else {
          // First Load
          // Add Separator
           const lastMsgInfo = rawItems.length > 0 ? rawItems[0] : null;
          let timeStr = "";
          if (lastMsgInfo && lastMsgInfo.created_at) {
             try {
                const date = new Date(lastMsgInfo.created_at);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, "0");
                const day = String(date.getDate()).padStart(2, "0");
                const hours = String(date.getHours()).padStart(2, "0");
                const minutes = String(date.getMinutes()).padStart(2, "0");
                timeStr = `${year}-${month}-${day} ${hours}:${minutes}`;
             } catch (e) {}
          }
          newHistoryBatch.push({
            id: Date.now() + 999999,
            role: "system",
            content: "以上是历史会话，可以重置会话清除",
            timestamp: timeStr,
          });
          messages.value = newHistoryBatch;
          nextTick(scrollToBottom);
        }
      }
    }
  } catch (e) {
    console.warn("Failed to load session history", e);
  } finally {
    if (requestSequence === historyRequestSequence) isLoadingHistory.value = false;
  }
};

const showQuotaStatusInChat = async () => {
  messages.value.push({
    id: Date.now(),
    role: "user",
    content: "/quota",
    timestamp: new Date().toISOString(),
  });
  await refreshQuota();
  messages.value.push({
    id: Date.now() + 1,
    role: "agent",
    agentName: "sys_quota",
    agentDisplayName: "系统助手",
    content: buildQuotaStatusMarkdown(quotaStatus.value),
    timestamp: new Date().toISOString(),
  });
  autoScrollEnabled.value = true;
  await nextTick();
  scrollToBottom(true);
};

// --- Logic ---
const handleSystemCommand = async (cmd: string): Promise<boolean> => {
  const normalizedCmd = normalizeAgentSwitchCommand(cmd, allowedAgents.value);
  if (isDatasetPortalSlashCommand(normalizedCmd)) {
    userInput.value = "";
    if (isRoutingSettingsLocked.value && !agentHasCapability("data_query")) {
      showToast("当前智能体不支持数据查询，无法打开数据门户", "warning");
      return true;
    }
    if (showPortalDrawer.value) {
      closePortalDrawer();
    } else {
      await openPortalDrawer();
    }
    return true;
  }
  if (isWorkspaceSlashCommand(normalizedCmd)) {
    userInput.value = "";
    toggleWorkspaceDrawer();
    return true;
  }
  if (isMyArtifactsSlashCommand(normalizedCmd)) {
    userInput.value = "";
    toggleMyArtifactsDrawer();
    return true;
  }
  if (isKnowledgePortalSlashCommand(normalizedCmd)) {
    userInput.value = "";
    if (isRoutingSettingsLocked.value && !agentHasCapability("knowledge_base")) {
      showToast("当前智能体不支持知识库能力，无法打开知识库中心", "warning");
      return true;
    }
    if (showKnowledgePortal.value) {
      closeKnowledgePortal();
    } else {
      await openKnowledgePortal();
    }
    return true;
  }
  if (normalizedCmd === "/switch_to_auto" || normalizedCmd === "/switch_agent_auto") {
    userInput.value = "";
    if (isRoutingSettingsLocked.value) {
      showToast("当前链接已锁定指定智能体，无法切换到智能委派", "warning");
      return true;
    }
    switchToAuto();
    return true;
  }
  if (normalizedCmd.startsWith("/switch_agent_expert?agent_id=")) {
    userInput.value = "";
    const agentId = normalizedCmd.split("?agent_id=")[1];
    if (agentId) {
      if (isRoutingSettingsLocked.value) {
        showToast("当前链接已锁定指定智能体，无法切换其他专家", "warning");
        return true;
      }
      switchToExpert(agentId);
    }
    return true;
  }
  switch (normalizedCmd) {
    case "/history":
      userInput.value = "";
      showHistorySidebar.value = !showHistorySidebar.value;
      return true;
    case "/settings":
      userInput.value = "";
      showSettings.value = true;
      return true;
    case "/quota":
    case "/tokens":
      userInput.value = "";
      await showQuotaStatusInChat();
      return true;
    case "/compact":
      userInput.value = "";
      await manualCompactEmbedContext();
      return true;
    case "/new":
    case "/clear": // legacy alias
      userInput.value = "";
      showConfirmModal.value = true;
      return true;
    case "/project":
      userInput.value = "";
      // 与新会话一致：先清空当前对话内容并换新 conversation_id，再打开项目资源配置
      messages.value = [];
      config.enableGrounding = true;
      generateNewConversation();
      postMessageToHost({
        type: "CONVERSATION_CHANGED",
        conversation_id: conversationId.value,
        clear_host_conversation_pin: true,
      });
      openResourceScopeModal();
      return true;
  }
  return false;
};
// UI Settings Actions
const setTheme = (theme: string) => {
  applyTheme(theme);
  config.theme = theme;
};
const setColor = (color: string) => {
  activeColor.value = color;
  applyTheme(config.theme, { "--primary-color": color });
};
// --- Actions ---
const copyMessage = async (content: string) => {
  if (!content) return;
  const ok = await copyTextSecure(content);
  if (ok) {
    showToast("已复制到剪贴板", "success");
  } else {
    console.error("Failed to copy");
    showToast("复制失败，请手动复制", "error");
  }
};

const exportData = async (traceId: string, format = 'xlsx') => {
  if (!traceId) return;
  try {
    // 导出端点现在登记产物到 ai_artifacts 并返回带鉴权 download_url（JSON，非文件流），
    // 由 /generated-files/{id} 以二进制 FileResponse 提供下载，避免 blob 方式误把 JSON 当文件打开。
    const response = await axios.get(`/api/v1/chat/export/data/${traceId}`, {
      params: { format }
    });
    const downloadUrl: string = response.data?.download_url;
    if (!downloadUrl) {
      throw new Error('missing download_url');
    }
    const href = resolveGeneratedFileHref(downloadUrl);
    const filename: string = response.data?.filename || `nanzi_export_${traceId.slice(0, 8)}.${format}`;
    const link = document.createElement('a');
    link.href = href;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast('数据导出成功', 'success');
  } catch (e) {
    console.error("Export failed", e);
    showToast('导出失败：未找到可导出数据', 'error');
  }
};
const regenerate = async () => {
  await sendPreparedMessage(async () => {
    const lastUserMsg = [...messages.value]
      .reverse()
      .find((m) => m.role === "user");
    if (!lastUserMsg) return null;
    const userIndex = messages.value.findIndex((message) => message.id === lastUserMsg.id);
    const remainingMessages = userIndex >= 0
      ? messages.value.slice(0, userIndex)
      : [...messages.value];
    const keepCount = remainingMessages.filter(isChatContextMessage).length;
    const content = splitUserMessageContent(lastUserMsg.content).userPart.trim();
    if (!content && !lastUserMsg.files?.length) return null;
    const files = (lastUserMsg.files || []).map((file) => ({ ...file }));
    const clientRequestId = createClientRequestId();
    if (!(await truncateServerHistory(keepCount))) return null;
    messages.value = remainingMessages;
    return { content, files, clientRequestId, reusableResultId: null };
  });
};
const handleFeedback = async (msg: Message, type: "up" | "down") => {
  const oldFeedback = msg.feedback;
  if (msg.feedback === type) {
    msg.feedback = null;
  } else {
    msg.feedback = type;
  }

  // 立即弹出提示 (乐观更新)
  if (msg.feedback) {
     showToast(msg.feedback === 'up' ? "感谢您的点赞！" : "已记录您的反馈，我们将持续改进。", "success");
  } else {
     showToast("已取消反馈", "info");
  }

  if (!msg.trace_id) {
    console.warn("Cannot post feedback to server: missing trace_id");
    // 如果是历史消息且缺失 trace_id，目前无法同步到后端，但前端可以保留点击态
    return;
  }

  try {
    await axios.post("/api/portal/chat/feedback", {
      trace_id: msg.trace_id,
      feedback: msg.feedback || "none", // Send "none" if un-selecting
      user_id: currentUser.value?.user_id || "anonymous"
    });
  } catch (error) {
    console.error("Failed to post feedback", error);
    msg.feedback = oldFeedback; // 失败时回退视觉状态
  }

  postMessageToHost({
    type: "USER_FEEDBACK",
    message_id: msg.id,
    trace_id: msg.trace_id,
    feedback: msg.feedback,
  });
};

const openModelCallStats = async (msg: any) => {
  currentStats.value = [];
  showStatsModal.value = true;
  loadingStats.value = true;
  try {
    const res = await axios.get(`/api/v1/chat/conversation/${conversationId.value}/model_calls`, {
      params: { trace_id: msg.trace_id }
    });
    if (res.data && res.data.data) {
      currentStats.value = res.data.data.stats || [];
    }
  } catch (err) {
    console.error("加载大模型调用明细失败:", err);
  } finally {
    loadingStats.value = false;
  }
};

const stopGeneration = () => {
  const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : null;
  if (conversationId.value) {
    void cancelConversationRun(conversationId.value, {
      traceId: lastMsg?.trace_id,
      headers: embedAuthHeaders(),
    }).finally(() => {
      void refreshCurrentRunStatus();
    });
  }
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  isProcessing.value = false;
  if (thoughtTimer) {
    clearInterval(thoughtTimer);
    thoughtTimer = null;
  }
  // Mark last thinking message as stopped
  if (lastMsg && lastMsg.isThinking) {
    lastMsg.isThinking = false;
    if (!lastMsg.content) {
      lastMsg.content = "[已停止生成]";
    } else {
      lastMsg.content += "\n\n[用户终止生成]";
    }
  }
};
const citationPopover = ref<{
  visible: boolean;
  citation: any;
  anchorRect: DOMRect | null;
  anchorEl: HTMLElement | null;
}>({
  visible: false,
  citation: null,
  anchorRect: null,
  anchorEl: null,
});

const closeCitationPopover = () => {
  citationPopover.value.visible = false;
  citationPopover.value.citation = null;
  citationPopover.value.anchorRect = null;
  citationPopover.value.anchorEl = null;
};

const openCitationPopover = (citation: any, event: MouseEvent | HTMLElement) => {
  const anchor = event instanceof HTMLElement ? event : (event.currentTarget as HTMLElement);
  if (!anchor) return;

  if (
    citationPopover.value.visible &&
    citationPopover.value.citation === citation
  ) {
    closeCitationPopover();
    return;
  }

  const rect = anchor.getBoundingClientRect();
  citationPopover.value = {
    visible: true,
    citation,
    anchorRect: new DOMRect(rect.x, rect.y, rect.width, rect.height),
    anchorEl: anchor,
  };
};

const resolveCitation = (msg: Message, citeId: string) => {
  if (!msg.citations || msg.citations.length === 0) return null;

  let target = msg.citations.find(
    (c) =>
      String(c.id) === String(citeId) ||
      String(c.chunk_id) === String(citeId) ||
      String(c.chunk_id)?.endsWith(String(citeId))
  );

  if (!target && /^\d+$/.test(citeId)) {
    const idx = parseInt(citeId);
    target = msg.citations[idx - 1] || msg.citations[idx];
  }

  return target || null;
};

const handleShowCitation = async (msg: Message, citeId: string, anchor?: HTMLElement) => {
  const target = resolveCitation(msg, citeId);
  if (!target) return;

  msg.isCitationsExpanded = true;
  await nextTick();
  const anchorEl = anchor || (document.querySelector(`[data-cite-id="${citeId}"]`) as HTMLElement);
  if (anchorEl) {
    anchorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    openCitationPopover(target, anchorEl);
  }
};

const ragPreviewVisible = ref(false);
const ragPreviewDatasetId = ref("");
const ragPreviewDocId = ref("");
const ragPreviewDocName = ref("");
const ragPreviewPageNo = ref<string | number>(1);
const ragPreviewContent = ref("");

const ragPreviewFileUrl = computed(() => {
  if (!ragPreviewDatasetId.value || !ragPreviewDocId.value) return "";
  const datasetId = encodeURIComponent(ragPreviewDatasetId.value);
  const docId = encodeURIComponent(ragPreviewDocId.value);
  return `/api/portal/ragflow/datasets/${datasetId}/documents/${docId}/file`;
});

const isOfficeDocument = computed(() => {
  const name = ragPreviewDocName.value.toLowerCase();
  return name.endsWith(".doc") || name.endsWith(".docx") || 
         name.endsWith(".xls") || name.endsWith(".xlsx") || 
         name.endsWith(".ppt") || name.endsWith(".pptx");
});

const handleViewOriginal = (citation: any) => {
  closeCitationPopover();
  if (citation.source_type === "web") {
    if (citation.link) {
      window.open(citation.link, "_blank");
    }
  } else {
    ragPreviewDatasetId.value = citation.dataset_id || "";
    ragPreviewDocId.value = citation.doc_id || "";
    ragPreviewDocName.value = citation.doc_name || "文件预览";
    ragPreviewPageNo.value = citation.page_no || 1;
    ragPreviewContent.value = citation.content || "";
    ragPreviewVisible.value = Boolean(ragPreviewDatasetId.value && ragPreviewDocId.value);
  }
};

const chatbiMonitorDialogOpen = ref(false);
const chatbiMonitorResultId = ref<string>();
const handleChatBIMonitorCreated = (payload: { created: boolean }) => {
  chatbiMonitorDialogOpen.value = false;
  showToast(payload.created === false ? "该结果的订阅已存在" : "查询订阅已创建，可在固化报表中管理", "success");
};

const handleChatBIResultAction = async (
  action: ChatBIInsightMeta["actions"][number],
  sourceMessage?: Message,
) => {
  if (action.id === "monitor") {
    chatbiMonitorResultId.value = action.result_id;
    chatbiMonitorDialogOpen.value = true;
    return;
  }
  if (action.id !== "brief") {
    if (!armDataQueryAgentForFollowup()) return;
    return handleQuickQuestion(action.query);
  }
  const assistantReport = sourceMessage?.content?.trim();
  if (!assistantReport) {
    showToast("未找到当前分析正文，请在本轮回复旁重试", "warning");
    return;
  }
  try {
    showToast("正在生成业务简报…", "info");
    const res = await axios.post("/api/portal/chatbi-briefs", {
      conversation_id: conversationId.value,
      result_id: action.result_id || sourceMessage?.chatbiInsight?.result_id,
      export_word: true,
      assistant_report: assistantReport,
      polish_with_llm: true,
    });
    const artifact = res.data?.data?.artifact;
    if (artifact?.download_url) {
      const link = document.createElement("a");
      link.href = artifact.download_url;
      link.download = artifact.filename || "ChatBI业务简报.docx";
      link.click();
    }
    showToast("业务简报已生成", "success");
  } catch (error: any) {
    showToast(error.response?.data?.detail || "业务简报生成失败", "error");
  }
};

/**
 * 仅给已经确认产生过数据结果的 assistant 消息附加快捷追问上下文。
 * 该上下文不会显示在 Markdown、用户气泡或 messages 中，只作为请求级路由提示。
 */
const quickContextForMessage = (msg: Message): QuickQuestionContext | undefined => {
  if (msg.role !== "agent") return undefined;

  const resultId = msg.chatbiInsight?.result_id || undefined;
  // reusableResultStatus 可能来自知识库或其他结果类型，不能单独证明这是 ChatBI。
  // hasDataOutput 和 chatbiInsight 都由服务端查数结果事件/历史字段确认。
  const hasVerifiedDataResult = msg.hasDataOutput === true || Boolean(msg.chatbiInsight?.result_id);
  if (!hasVerifiedDataResult) return undefined;

  return {
    source: "chatbi_result",
    result_id: resultId,
    requires_fresh_data: true,
  };
};

const handleQuickQuestion = async (
  content: string | QuickQuestionPayload,
  action: "send" | "fill" = "send",
  sourceContent?: string,
) => {
  const question = typeof content === "string" ? content : content.question;
  const quickContext = action === "send" && typeof content !== "string"
    ? content.quick_context
    : undefined;
  if (!question) return;
  if (action === "send" && (isProcessing.value || remoteRunActive.value || sendLocked.value)) return;
  const selectedSource = sourceContent?.trim();
  const nextContent = selectedSource
    ? `${question}${USER_MESSAGE_CONTEXT_DIVIDER}【被点击的 AI 回复】\n${selectedSource}`
    : question;
  if (action === "send") {
    await sendMessage({ content: nextContent, quickContext });
  } else {
    userInput.value = nextContent;
  }
};

const handleGroundingAction = async (
  payload: GroundingBlockedPayload | undefined,
  action: GroundingBlockedAction,
) => {
  if (!payload || isProcessing.value || remoteRunActive.value || sendLocked.value) return;
  if (action.kind === "grounding_retry") {
    const groundingAction = {
      ...(action.payload || {}),
      type: "retry",
    };
    await sendMessage({ content: payload.retry_query, groundingAction });
    return;
  }
  if (action.kind === "grounding_method") {
    const groundingAction = {
      ...(action.payload || {}),
      type: "method",
    };
    await sendMessage({ content: String(action.payload?.message || ""), groundingAction });
    return;
  }
  if (action.kind === "send_message") {
    await handleQuickQuestion(String(action.payload?.message || ""));
  }
};

const portalLoadingTips = [
  "正在为数据集唤醒大模型并进行资源初始化... 🧠",
  "AI 正在深度解析物理表的业务语义与指标口径... 📊",
  "首次加载需探索物理库表，耗时稍长（约15-30秒），请耐心稍候喔 ✨",
  "正在基于大模型智构最适合该数据集的场景分析提问... 🚀",
  "南孜大模型正在努力推理计算中，马上就好... 🔄",
];
const currentPortalLoadingTip = ref(portalLoadingTips[0]);
let portalLoadingTipTimer: ReturnType<typeof setInterval> | null = null;

const startPortalLoadingTips = () => {
  if (portalLoadingTipTimer) clearInterval(portalLoadingTipTimer);
  let index = 0;
  currentPortalLoadingTip.value = portalLoadingTips[0];
  portalLoadingTipTimer = setInterval(() => {
    index = (index + 1) % portalLoadingTips.length;
    currentPortalLoadingTip.value = portalLoadingTips[index];
  }, 4000);
};

const stopPortalLoadingTips = () => {
  if (portalLoadingTipTimer) {
    clearInterval(portalLoadingTipTimer);
    portalLoadingTipTimer = null;
  }
};

const {
  showPortalDrawer,
  portalNavigationPayload,
  portalLoading,
  portalBackgroundRefreshing,
  portalKeepOpenOnQuestion,
  portalPinned,
  openPortalDrawer: rawOpenPortalDrawer,
  closePortalDrawer,
  refreshPortalNavigation,
  handlePortalQuickQuestion,
  recordDatasetMenuQuestionClick,
  clearDatasetMenuQuestionClick,
  prefetchPortalNavigationIfEligible,
  fetchDatasetMenuNavigationPayload,
  disposePortalTimers,
} = useDatasetPortal({
  getAuthHeaders: () => embedAuthHeaders() || {},
  showToast,
  onQuickQuestion: handleQuickQuestion,
  hasDataQueryAgent,
  keepOpenStorageKey: "embed_portal_keep_open",
  pinStorageKey: "embed_portal_pinned",
  onPortalLoadingChange: (loading) => {
    if (loading) startPortalLoadingTips();
    else stopPortalLoadingTips();
  },
});

const openPortalDrawer = async () => {
  if (isRoutingSettingsLocked.value && !agentHasCapability("data_query")) {
    showToast("当前智能体不支持数据查询，无法打开数据门户", "warning");
    return;
  }
  // 只后台补齐元数据集，不 await RAGFlow/Skills 整包（易 502 拖慢打开）
  void ensureMountableMetadataDatasets();
  await rawOpenPortalDrawer();
};

const {
  showKnowledgePortal,
  knowledgePinned,
  knowledgeKeepOpenOnQuestion,
  hallucinationCheckEnabled,
  knowledgeSimilarityThreshold,
  knowledgeVectorWeight,
  knowledgeMetadataTopK,
  knowledgeGeneratedAt,
  datasets: knowledgeDatasets,
  loadingDatasets: loadingKnowledgeDatasets,
  datasetLoadError: knowledgeLoadError,
  activeDatasetIds,
  datasetRecommendations,
  pinnedDatasetIds,
  datasetDocuments,
  documentRecommendations,
  toggleDatasetPinned,
  fetchDatasetDocuments,
  fetchDocumentRecommendations,
  fetchDatasets,
  fetchRecommendations,
  syncActiveDatasetsFromInput,
  toggleDatasetActive,
  openKnowledgePortal: rawOpenKnowledgePortal,
  closeKnowledgePortal
} = useKnowledgePortal({
  showToast,
  onOpenAnotherPortal: () => {
    closePortalDrawer();
  }
});

const openKnowledgePortal = async () => {
  if (isRoutingSettingsLocked.value && !agentHasCapability("knowledge_base")) {
    showToast("当前智能体不支持知识库能力，无法打开知识库中心", "warning");
    return;
  }
  await rawOpenKnowledgePortal();
};

watch(showPortalDrawer, (val) => {
  if (val) {
    closeKnowledgePortal();
  } else {
    stopPortalLoadingTips();
  }
});

// 监听上传文件的变更，保持知识库与数据集激活状态在抽屉卡片里是最新同步的
watch(
  () => chatInputRef.value?.uploadedFiles,
  () => {
    syncActiveDatasetsFromInput(chatInputRef.value);
    syncActiveMetadataDatasetsFromInput(chatInputRef.value);
  },
  { deep: true }
);



const pinnedDrawerDockOffsetRem = (exclude?: "portal" | "workspace" | "memory" | "knowledge" | "browser" | "webPreview") => {
  let rem = 0;
  if (exclude !== "portal" && showPortalDrawer.value && portalPinned.value) rem += 28;
  if (exclude !== "knowledge" && showKnowledgePortal.value && knowledgePinned.value) rem += 28;
  if (exclude !== "workspace" && showWorkspaceDrawer.value && workspacePinned.value) rem += 28;
  if (exclude !== "memory" && showMemoryDrawer.value && memoryPinned.value) rem += 28;
  if (exclude !== "browser" && browserPanelVisible.value && browserPinned.value && !isMobile.value) {
    rem += browserPanelWidthReactive.value / 16;
  }
  if (exclude !== "webPreview" && webPreviewVisible.value && webPreviewPinned.value && !isMobile.value) {
    rem += webPreviewPanelWidthReactive.value / 16;
  }
  return rem;
};

const pinnedDrawerRightRem = computed(() => pinnedDrawerDockOffsetRem());
const saveReportModalOverlayStyle = computed(() => {
  const rem = pinnedDrawerRightRem.value;
  return { right: rem > 0 ? `${rem}rem` : "0" };
});
const saveReportModalOverlayClass = computed(() => {
  const isPinned = (showPortalDrawer.value && portalPinned.value) || (showKnowledgePortal.value && knowledgePinned.value);
  return isPinned ? 'right-[28rem]' : 'right-0';
});

// 响应式抽屉宽度 refs（由各抽屉组件通过 v-model:drawerWidth / v-model:canvasWidth 实时同步）
const portalDrawerWidthReactive = ref(448);
const knowledgeDrawerWidthReactive = ref(448);
const workspaceDrawerWidthReactive = ref(448);
const canvasPinnedWidthReactive = ref(520);
const browserPanelWidthReactive = ref(520);
const webPreviewPinned = ref(false);
const webPreviewPanelWidthReactive = ref(448);

const portalDrawerWidthPx = computed(() => {
  if (!showPortalDrawer.value || !portalPinned.value || isMobile.value) return 0;
  return portalDrawerWidthReactive.value;
});

const knowledgeDrawerWidthPx = computed(() => {
  if (!showKnowledgePortal.value || !knowledgePinned.value || isMobile.value) return 0;
  return knowledgeDrawerWidthReactive.value;
});

const canvasPinnedWidthPx = computed(() => {
  if (!canvasVisible.value || !canvasPinned.value || isMobile.value) return 0;
  return canvasPinnedWidthReactive.value;
});

const workspaceDrawerWidthPx = computed(() => {
  if (!showWorkspaceDrawer.value || !workspacePinned.value || isMobile.value) return 0;
  return workspaceDrawerWidthReactive.value;
});

const browserPanelWidthPx = computed(() => {
  if (!browserPanelVisible.value || !browserPinned.value || isMobile.value) return 0;
  return browserPanelWidthReactive.value;
});

const webPreviewPanelWidthPx = computed(() => {
  if (!webPreviewVisible.value || !webPreviewPinned.value || isMobile.value) return 0;
  return webPreviewPanelWidthReactive.value;
});

const webPreviewDockRightPx = computed(() => pinnedDrawerDockOffsetRem("webPreview") * 16);

const totalPinnedDrawerPx = computed(() => {
  if (isMobile.value || windowWidth.value < 768) return 0;
  let px = 0;
  px += portalDrawerWidthPx.value;
  px += knowledgeDrawerWidthPx.value;
  px += workspaceDrawerWidthPx.value;
  px += canvasPinnedWidthPx.value;
  px += browserPanelWidthPx.value;
  px += webPreviewPanelWidthPx.value;
  if (showMemoryDrawer.value && memoryPinned.value) px += 448;
  return px;
});

const pinnedDrawerMarginStyle = computed(() => {
  const px = totalPinnedDrawerPx.value;
  return px > 0 ? { marginRight: `min(${px}px, 100vw)` } : {};
});

const workspacePinnedDockClass = computed(() => {
  const rem = pinnedDrawerDockOffsetRem("workspace");
  return rem > 0 ? `right-[${rem}rem]` : "right-0";
});

const memoryPinnedDockClass = computed(() => {
  const rem = pinnedDrawerDockOffsetRem("memory");
  return rem > 0 ? `right-[${rem}rem]` : "right-0";
});

const refreshDatasetMenuNavigation = async (msg: Message) => {
  if (datasetMenuLoading.value || isProcessing.value) {
    return;
  }
  datasetMenuLoading.value = true;
  isProcessing.value = true;
  try {
    const payload = await fetchDatasetMenuNavigationPayload(true);
    msg.datasetNavigation = payload;
    msg.content = payload?.markdown || "当前暂无可展示的数据集导航，请联系管理员开通数据权限。";
    isProcessing.value = false;
    if (payload?.llm_generation_failed) {
      const detail = String(payload.llm_error_message || "").trim();
      const hint = detail ? `：${detail}` : "";
      showToast(`AI 模型暂不可用，仍为基础场景目录${hint}`, "error");
    } else {
      showToast("数据门户刷新成功", "success");
    }
    await nextTick();
    scrollToBottom(true);
  } catch (error) {
    console.warn("Failed to refresh dataset menu navigation", error);
    showToast("刷新数据门户失败，请稍后重试", "error");
    if (msg.datasetNavigation) {
      msg.datasetNavigation = { ...msg.datasetNavigation, _failed_at: new Date().toISOString() };
    }
  } finally {
    datasetMenuLoading.value = false;
    isProcessing.value = false;
  }
};

const handleSaveReportFromMessage = (msg: Message) => {
  const sql = resolveSavableSqlFromMessage(msg);
  if (sql) openSaveReportModal(sql, msg);
};

const addEmbedLogFromStream = (msg: Message, data: any) => {
  if (!msg.logs) msg.logs = [];
  const logId = data.id || Date.now() + Math.random();
  const existingIdx = msg.logs.findIndex((l) => l.id === logId);
  const title = data.title || "";
  let category: LogEntry["category"] = data.category || "default";
  if (category === "default") {
    if (title.includes("路由")) category = "router";
    else if (title.includes("SQL") || title.includes("sql") || title.includes("数据")) category = "sql";
    else if (title.includes("知识") || title.includes("检索") || title.includes("引用") || title.includes("来源") || title.includes("分析")) category = "knowledge";
    else if (title.includes("工具") || title.includes("调用")) category = "tool";
    else if (title.includes("意图") || title.includes("轮次分类")) category = "intent";
    else if (title.includes("模型")) category = "model";
    else if (title.includes("权限") || title.includes("permission") || title.includes("确认")) category = "permission";
  }
  if (data.turn_type && category === "intent") {
    msg.turnType = data.turn_type;
    if (msg.isThinking) {
      msg.isThoughtExpanded = config.expandThoughts;
    }
  }
  if (existingIdx > -1) {
    const currentLog = msg.logs[existingIdx];
    if (!currentLog) return;
    const nextStatus = (data.status as LogEntry["status"]) || currentLog.status || "success";
    // router_log 是路由结果摘要，携带的 execution_time_ms 是外层目标配置解析总耗时；
    // 路由阶段已经记录过 route:target_selection 时，不能用这个总耗时覆盖子阶段耗时。
    const preserveRouteSelectionDuration =
      logId === "route:target_selection"
      && category === "router"
      && currentLog.execution_time_ms !== undefined
      && currentLog.execution_time_ms !== null;
    const preserveRouteSelectionTitle =
      logId === "route:target_selection"
      && category === "router"
      && Boolean(currentLog.title);
    const execution_time_ms =
      (preserveRouteSelectionDuration ? currentLog.execution_time_ms : data.execution_time_ms) ??
      (nextStatus !== "pending"
        ? resolveStreamLogDurationMs(currentLog, data.execution_time_ms)
        : currentLog.execution_time_ms);
    msg.logs[existingIdx] = {
      ...currentLog,
      parent_id: data.parent_id ?? currentLog.parent_id,
      title: preserveRouteSelectionTitle ? currentLog.title : (data.title || currentLog.title),
      details: data.details ?? currentLog.details,
      status: nextStatus,
      error_reason: data.error_reason ?? currentLog.error_reason,
      category: category !== "default" ? category : currentLog.category,
      execution_time_ms: execution_time_ms ?? currentLog.execution_time_ms,
      elapsed_time_ms: data.elapsed_time_ms ?? currentLog.elapsed_time_ms,
      started_at: currentLog.started_at ?? (data.status === "pending" ? Date.now() : data.started_at),
      subagent: data.subagent !== undefined
        ? normalizeSubagentTraceMeta(data.subagent)
        : currentLog.subagent,
      tool_name: data.tool_name ?? currentLog.tool_name,
      file_metadata: data.file_metadata ?? currentLog.file_metadata,
      resolution_status: data.resolution_status ?? currentLog.resolution_status,
      rowFilterApplied: data.row_filter_applied === true || currentLog.rowFilterApplied,
    };
    syncProcessTimelineLog(msg, {
      ...data,
      id: logId,
      category: category !== "default" ? category : currentLog.category,
      execution_time_ms: execution_time_ms ?? currentLog.execution_time_ms,
      started_at: currentLog.started_at ?? data.started_at,
    });
    return;
  }
  msg.logs.push({
    id: logId,
    parent_id: data.parent_id,
    title: data.title || "Log Info",
    details: data.details || "",
    status: (data.status as any) || "success",
    error_reason: data.error_reason,
    isExpanded: false,
    category,
    execution_time_ms: data.execution_time_ms ?? null,
    elapsed_time_ms: data.elapsed_time_ms ?? null,
    started_at: data.status === "pending" ? Date.now() : (data.started_at ?? null),
    subagent: normalizeSubagentTraceMeta(data.subagent),
    tool_name: data.tool_name,
    file_metadata: data.file_metadata,
    resolution_status: data.resolution_status,
    rowFilterApplied: data.row_filter_applied === true,
  });
  syncProcessTimelineLog(msg, { ...data, id: logId, category }, category);
};

const applyReusableResultStatusEvent = (msg: Message, data: any): boolean => {
  if (data?.type !== "reusable_result_status") return false;
  // 复用本轮结束时可能还会保存一个新的结果；不能让 saved 覆盖 reused，
  // 否则顶部“引用提示”会在回答完成后消失。
  if (data.status === "saved" && msg.reusableResultStatus?.status === "reused") return true;
  msg.reusableResultStatus = {
    status: String(data.status || "fallback"),
    resultId: data.result_id ? String(data.result_id) : null,
    originName: data.origin_name ? String(data.origin_name) : null,
  };
  if (data.status === "reused") {
    reusedReusableResultId.value = data.result_id ? String(data.result_id) : null;
  }
  return true;
};

const applyPermissionStreamEvent = (msg: Message, data: any) => {
  applyStreamTraceId(msg, data);
  if (data.agent_name && !msg.agentName) msg.agentName = data.agent_name;
  if (data.agent_display_name && !msg.agentDisplayName) msg.agentDisplayName = data.agent_display_name;

  if (applyReusableResultStatusEvent(msg, data)) return;

  if (applyChatBIInsightEvent(msg, data) || applyChatBIMetadataGuideEvent(msg, data) || applyAgentHandoffEvent(msg, data)) return;

  if (dispatchAgentscopeStreamEvent(msg, data, addEmbedLogFromStream, messages.value, handleBashEnvEvent)) {
    if (data.type === "error") {
      if (msg.pendingPermission) msg.pendingPermission.status = "error";
      if (msg.pendingExternalExecution) msg.pendingExternalExecution.status = "error";
      msg.isThinking = false;
      applyStreamErrorMessage(msg, data);
    } else if (
      data.content &&
      data.type !== "reasoning_content" &&
      data.type !== "process_narration" &&
      data.type !== "process_narration_commit" &&
      data.type !== "process_narration_promote" &&
      data.type !== "answer_delta" &&
      data.type !== "retraction"
    ) {
      const piece = sanitizeStreamContent(String(data.content || ""));
      if (piece) {
        if (msg.isThoughtExpanded && !msg.content) msg.isThoughtExpanded = false;
        appendAssistantBodyDelta(msg, piece);
        if (msg.isThinking) msg.isThinking = false;
        resetStallTimer();
      }
    }
    return;
  }

  if (data.type === "log") {
    addEmbedLogFromStream(msg, data);
  } else if (mergeStreamCitations(msg, data)) {
    // Citations are merged and de-duplicated by the shared stream normalizer.
  } else if (data.type === "router_log") {
    const agentName = data.selected_agent || "Unknown";
    const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
    const routerId = data.id || "route:target_selection";
    addEmbedLogFromStream(msg, {
      id: routerId,
      title: "目标专家匹配完成",
      details: `已完成目标专家匹配。\n目标专家: ${agentName} ${conf}`.trim(),
      status: "success",
      isRouter: true,
      category: "router",
      parent_id: data.parent_id || "route:target_config",
      execution_time_ms: data.execution_time_ms ?? null,
    });
  } else if (data.type === "meta") {
    if (data.agent_name) msg.agentName = data.agent_name;
    if (data.agent_type) msg.agentType = data.agent_type;
    if (data.agent_display_name) msg.agentDisplayName = data.agent_display_name;
    if (data.turn_type) msg.turnType = data.turn_type;
    if (data.prompt_tokens !== undefined) msg.prompt_tokens = data.prompt_tokens;
    if (data.completion_tokens !== undefined) msg.completion_tokens = data.completion_tokens;
    if (data.has_data_output) msg.hasDataOutput = true;
    if (data.permission_notice) msg.permissionNotice = data.permission_notice;
  } else if (data.type === "error") {
    if (msg.pendingPermission) msg.pendingPermission.status = "error";
    msg.isThinking = false;
    applyStreamErrorMessage(msg, data);
  } else if (data.content) {
    const piece = sanitizeStreamContent(String(data.content || ""));
    if (piece) {
      if (msg.isThoughtExpanded && !msg.content) msg.isThoughtExpanded = false;
      appendAssistantBodyDelta(msg, piece);
      if (msg.isThinking) msg.isThinking = false;
      resetStallTimer();
    }
  }
};

const submitPendingExternalExecution = async (msg: Message) => {
  const pending = msg.pendingExternalExecution;
  if (!pending || pending.status !== "pending" || pending.isSubmitting) return;
  pending.isSubmitting = true;
  isProcessing.value = true;
  msg.isThinking = true;
  startThoughtTimer(msg);
  msg.isThoughtExpanded = config.expandThoughts;
  msg.thinkingText = "正在提交外部执行结果...";
  resetStallTimer();

  try {
    await resumeExternalExecutionStream({
      requestId: pending.external_execution_request_id,
      toolCall: pending.tool_call,
      output: pending.outputDraft || "(empty external result)",
      headers: embedAuthHeaders() || {},
      credentials: "include",
      onEvent: (data) => applyPermissionStreamEvent(msg, data),
    });
  } catch (error: any) {
    pending.status = "error";
    msg.content += `\n[外部执行恢复失败: ${error.message || "Unknown error"}]`;
  } finally {
    pending.isSubmitting = false;
    isProcessing.value = msg.pendingExternalExecution?.status === "pending" || msg.pendingPermission?.status === "pending";
    msg.isThinking = false;
    clearStallTimer();
    showStalledPrompt.value = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    if (msg.logs) {
      msg.logs.forEach((l) => {
        if (l.status === "pending" && l.category !== "permission" && l.category !== "external") l.status = "success";
      });
    }
    scrollToBottom();
    nextTick(() => {
      if (!isMobile.value) chatInputRef.value?.focus();
    });
  }
};

const submitBusinessConfirmation = async (
  msg: Message,
  payload: { confirmed: boolean; fields: BusinessConfirmationField[] },
) => {
  const card = msg.businessConfirmation;
  if (!card || card.status !== "pending" || isProcessing.value) return;
  const content = buildBusinessConfirmationUserMessage(
    payload.confirmed,
    card.confirmation_id,
    payload.fields,
  );
  card.fields = payload.fields.map((field) => ({ ...field }));
  card.status = "submitted";
  card.decision = payload.confirmed ? "confirmed" : "cancelled";
  userInput.value = content;
  await sendMessage();
};

const submitUserQuestion = async (
  msg: Message,
  payload: { selectedOptionIds: string[]; customInput: string; cancelled: boolean },
) => {
  const card = msg.userQuestion;
  if (!card || card.status !== "pending" || isProcessing.value) return;
  const content = buildUserQuestionUserMessage(
    card.question_id,
    payload.selectedOptionIds,
    payload.customInput,
    payload.cancelled,
    card.question,
    card.options,
  );
  card.selected_option_ids = [...payload.selectedOptionIds];
  card.custom_input = payload.customInput;
  card.status = payload.cancelled ? "cancelled" : "submitted";
  userInput.value = content;
  await sendMessage();
};

const confirmPendingPermission = async (msg: Message, confirmed: boolean) => {
  const pending = msg.pendingPermission;
  if (!pending || pending.status !== "pending" || pending.isSubmitting) return;
  pending.isSubmitting = true;
  isProcessing.value = true;
  if (confirmed) {
    msg.isThinking = true;
    startThoughtTimer(msg);
    msg.isThoughtExpanded = config.expandThoughts;
    msg.thinkingText = "正在继续执行...";
    resetStallTimer();
  }

  try {
    const response = await fetch(`/api/v1/chat/permissions/${pending.permission_request_id}/confirm`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(embedAuthHeaders() || {}),
      },
      body: JSON.stringify({ confirmed }),
      credentials: "include",
    });
    if (!response.ok) throw new Error(response.statusText);
    const reader = response.body?.getReader();
    if (!reader) throw new Error("No body");
    const decoder = new TextDecoder();
    const parser = createSseLineParser();
    let isPermissionStreamDone = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const dataLines = parser.feed(decoder.decode(value, { stream: true }));
      for (const dataStr of dataLines) {
        if (dataStr === "[DONE]") {
          isPermissionStreamDone = true;
          break;
        }
        applyPermissionStreamEvent(msg, JSON.parse(dataStr));
      }
      scrollToBottom();
      if (isPermissionStreamDone) {
        try {
          await reader.cancel();
        } catch {
          // ignore
        }
        break;
      }
    }
    for (const dataStr of parser.flush()) {
      if (dataStr !== "[DONE]") applyPermissionStreamEvent(msg, JSON.parse(dataStr));
    }
  } catch (error: any) {
    pending.status = "error";
    msg.content += `\n[工具确认失败: ${error.message || "Unknown error"}]`;
  } finally {
    pending.isSubmitting = false;
    isProcessing.value = msg.pendingPermission?.status === "pending" || msg.pendingExternalExecution?.status === "pending";
    msg.isThinking = false;
    clearStallTimer();
    showStalledPrompt.value = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    if (msg.logs) {
      msg.logs.forEach((l) => {
        if (l.status === "pending" && l.category !== "permission") l.status = "success";
      });
    }
    scrollToBottom();
    nextTick(() => {
      if (!isMobile.value) chatInputRef.value?.focus();
    });
  }
};

const tryLocalChartOptionPatch = (userText: string): boolean => {
  const q = userText.toLowerCase().trim();
  let newType: 'line' | 'bar' | 'pie' | null = null;
  if (/改(成|为)折线图/.test(q) || /换成折线/.test(q)) {
    newType = 'line';
  } else if (/改(成|为)柱状图/.test(q) || /换成柱状/.test(q)) {
    newType = 'bar';
  } else if (/改(成|为)饼图/.test(q) || /换成饼图/.test(q)) {
    newType = 'pie';
  }

  const isRedPatch = /标红/.test(q);

  if (!newType && !isRedPatch) {
    return false;
  }

  // Find the last agent message with a chart block
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i];
    if (!msg) continue;
    if (msg.role === 'agent' && msg.content) {
      const chartRegex = /(<chart>([\s\S]*?)<\/chart>)|(```(?:chart|echarts)\s*([\s\S]*?)```)/gi;
      const match = chartRegex.exec(msg.content);
      if (match) {
        const fullMatch = match[0];
        const jsonContent = (match[2] || match[4] || "").trim();
        if (!jsonContent) continue;
        try {
          const option = JSON.parse(jsonContent);
          if (option.series) {
            if (newType) {
              if (Array.isArray(option.series)) {
                option.series = option.series.map((s: any) => ({ ...s, type: newType }));
              } else if (typeof option.series === 'object') {
                option.series.type = newType;
              }
              if (newType === 'pie') {
                delete option.xAxis;
                delete option.yAxis;
              }
            }
            if (isRedPatch) {
              if (Array.isArray(option.series)) {
                option.series = option.series.map((s: any) => ({
                  ...s,
                  itemStyle: { ...s.itemStyle, color: '#ef4444' }
                }));
              } else if (typeof option.series === 'object') {
                option.series.itemStyle = { ...option.series.itemStyle, color: '#ef4444' };
              }
            }

            const updatedJson = JSON.stringify(option, null, 2);
            let updatedMatch = "";
            if (match[1]) {
              updatedMatch = `<chart>\n${updatedJson}\n</chart>`;
            } else {
              updatedMatch = `\`\`\`chart\n${updatedJson}\n\`\`\``;
            }

            msg.content = msg.content.replace(fullMatch, updatedMatch);
            messages.value.push({
              id: Date.now(),
              role: "user",
              content: userText,
              timestamp: new Date().toISOString(),
            });
            messages.value.push({
              id: Date.now() + 1,
              role: "agent",
              content: `✨ 已为您本地秒级重绘图表，将图表形式调整为：${newType === 'line' ? '折线图' : newType === 'bar' ? '柱状图' : newType === 'pie' ? '饼图' : '标红调整'}。`,
              timestamp: new Date().toISOString(),
            });
            return true;
          }
        } catch (err) {
          console.error("Failed to parse or patch ECharts option locally:", err);
        }
      }
    }
  }
  return false;
};

const captureSendSnapshot = (overrides: ChatSendOverrides = {}): ChatSendSnapshot => ({
  content: String(overrides.content ?? userInput.value).trim(),
  files: overrides.files
    ? overrides.files.map((file) => ({ ...file }))
    : (chatInputRef.value?.uploadedFiles ? Array.from(chatInputRef.value.uploadedFiles) as ChatFile[] : [])
        .map((file) => ({ ...file })),
  clientRequestId: overrides.clientRequestId || createClientRequestId(),
  groundingAction: overrides.groundingAction ? { ...overrides.groundingAction } : undefined,
  reusableResultId: overrides.reusableResultId !== undefined
    ? (overrides.reusableResultId || undefined)
    : (selectedReusableResultId.value || undefined),
  quickContext: overrides.quickContext ? { ...overrides.quickContext } : undefined,
});

const sendPreparedMessage = async (
  prepare: () => Promise<ChatSendSnapshot | null>,
) => runSendExclusive(async () => {
  if (isProcessing.value || remoteRunActive.value) return;
  const snapshot = await prepare();
  if (!snapshot) return;
  return sendMessageInternal(snapshot);
});

const sendMessage = async (overrides: ChatSendOverrides = {}) => runSendExclusive(async () => {
  if (isProcessing.value || remoteRunActive.value) return;
  return sendMessageInternal(captureSendSnapshot(overrides));
});

const sendMessageInternal = async (snapshot: ChatSendSnapshot) => {
  const { content, files } = snapshot;
  if ((!content && files.length === 0) || isProcessing.value || remoteRunActive.value) return;

  // 尽早消费「强制查数智能体」标记，避免中途 return 后泄漏到下一轮普通提问
  const forcedDataAgentIdForTurn = forceDataQueryAgentOnce.value ? resolvePreferredDataQueryAgentId() : "";
  forceDataQueryAgentOnce.value = false;
  const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value];

  const quotaBlock = await ensureCanSend();
  if (quotaBlock) {
    showToast(quotaBlock, "error");
    return;
  }

  if (files.length === 0 && tryLocalChartOptionPatch(content)) {
    userInput.value = "";
    showCommandMenu.value = false;
    nextTick(() => scrollToBottom(true));
    return;
  }

  const messageContent = files.length > 0 ? appendAttachmentContext(content, files) : content;

  // 全局兜底：确保一定存在会话 ID
  if (!conversationId.value) {
      generateNewConversation();
  }

  if (await handleSystemCommand(content)) {
    userInput.value = "";
    showCommandMenu.value = false;
    return;
  }
  userInput.value = "";
  showCommandMenu.value = false;

  // 1. User Message（content 含 --- 分隔的隐式系统指令，气泡内分区展示）
  messages.value.push({
    id: Date.now(),
    role: "user",
    content: messageContent,
    files: files.length > 0 ? files : undefined,
    timestamp: new Date().toISOString(),
  });
  if (chatInputRef.value) {
    chatInputRef.value.uploadedFiles = [];
  }
  isProcessing.value = true;
  resetStallTimer();
  // 2. Agent Placeholder
  const agentMsg = ref<Message>({
    id: Date.now() + 1,
    role: "agent",
    content: "",
    isThinking: true,
    thinkingText: "正在连接服务…",
    logs: [],
    thoughtStartTime: Date.now(),
    thoughtDuration: "0.0",
    isThoughtExpanded: config.expandThoughts,
    isCitationsExpanded: false,
    timestamp: new Date().toISOString(),
  });
  messages.value.push(agentMsg.value);
  startStalePendingTimer(agentMsg.value);
  // 新一轮发送：恢复自动跟随（避免上一轮「向上滚动」导致本轮仍不跟底）
  autoScrollEnabled.value = true;
  showNewMessageHint.value = false;
  await nextTick();
  scrollToBottom(true);
  requestAnimationFrame(() => scrollToBottom(true));
  const ltmIgnoredVal = ignoreLtmThisTurn.value;
  ignoreLtmThisTurn.value = false;
  activeLtmPreference.value = null;

  // Start thought timer
  startThoughtTimer(agentMsg.value);
  // 3. API Call
  // SSE 可能因切后台/网络变化提前结束；在状态接口确认释放前继续阻止新一轮发送。
  remoteRunActive.value = true;
  abortController = new AbortController();
  // 首片正文立即显示，后续正文按帧合并更新。
  let pendingContentBuffer = "";
  let contentRafId: number | null = null;

  const flushContentBuffer = () => {
    if (pendingContentBuffer) {
      appendAssistantBodyDelta(agentMsg.value, pendingContentBuffer);
      pendingContentBuffer = "";
      scrollToBottom();
    }
    if (contentRafId !== null) {
      cancelAnimationFrame(contentRafId);
      contentRafId = null;
    }
  };

  const queueContentDelta = (piece: string) => {
    if (!piece) return;
    // 首片正文立即显示。
    if (!agentMsg.value.content && !pendingContentBuffer) {
      appendAssistantBodyDelta(agentMsg.value, piece);
      scrollToBottom();
      return;
    }
    pendingContentBuffer += piece;
    if (contentRafId === null) {
      contentRafId = requestAnimationFrame(flushContentBuffer);
    }
  };

  // 正文在公共 dispatcher 之前消费，避免 answer_delta 绕过缓冲。
  // 撤回使缓冲失效；其他事件先刷出正文，保持 SSE 顺序。
  const handleBufferedBodyEvent = (data: Record<string, any>): boolean => {
    if (data.type === "retraction") {
      pendingContentBuffer = "";
      if (contentRafId !== null) {
        cancelAnimationFrame(contentRafId);
        contentRafId = null;
      }
      return false;
    }
    if (data.type === "answer" || data.type === "answer_delta") {
      const piece = data.type === "answer_delta"
        ? String(data.content || "")
        : sanitizeStreamContent(String(data.content || ""));
      if (piece) {
        queueContentDelta(piece);
        agentMsg.value.isThinking = false;
        if (thoughtTimer) {
          clearInterval(thoughtTimer);
          thoughtTimer = null;
        }
      }
      return true;
    }
    if (data.type) flushContentBuffer();
    return false;
  };

  try {
    const mountedKnowledgeDatasetIds = resourceScope.value.knowledge_bases.map((item: any) => String(item.id || '').trim()).filter(Boolean);
    const knowledgeDatasetIds = mountedKnowledgeDatasetIds.length > 0 ? mountedKnowledgeDatasetIds : collectKnowledgeDatasetIds();
    const body: Record<string, unknown> = {
      messages: buildOutboundMessages(),
      stream: true,
      agent_id: forcedDataAgentIdForTurn || ((config.routingMode === "expert" && config.expertAgentId) ? config.expertAgentId : (config.overrideAgentId || config.agentId)),
      enable_multi_agent: config.enableMultiAgent,
      conversation_id: conversationId.value,
      debug_options: {
        injected_context: injectedContext.value,
        model: config.overrideModel || undefined,
        enable_sql_plan: config.enableSqlPlan,
        grounding_enabled: config.enableGrounding,
        grounding_block_mode: config.groundingBlockMode,
        browser_session_id: browserSessionId.value || undefined,
        ignore_ltm: ltmIgnoredVal,
        hallucination_check: hallucinationCheckEnabled.value || undefined,
        knowledge_ragflow_similarity_threshold: knowledgeSimilarityThreshold.value,
        knowledge_ragflow_vector_weight: knowledgeVectorWeight.value,
        knowledge_ragflow_metadata_top_k: knowledgeMetadataTopK.value,
        resource_scope: resourceScope.value,
      },
      permission_options: {
        approval_mode: config.approvalMode || "ask",
      },
    };
    if (thinkingEnableOverride.value !== null) {
      (body.debug_options as Record<string, unknown>).thinking_enable = thinkingEnableOverride.value;
    }
    if (reasoningEffortOverride.value !== null) {
      (body.debug_options as Record<string, unknown>).reasoning_effort = reasoningEffortOverride.value;
    }
    if (knowledgeDatasetIds.length > 0) {
      body.knowledge_dataset_ids = knowledgeDatasetIds;
    }
    if (turnMetadataDatasetIds.length > 0) {
      body.metadata_dataset_ids = turnMetadataDatasetIds;
    }
    if (snapshot.groundingAction) body.grounding_action = snapshot.groundingAction;
    if (snapshot.reusableResultId) body.reusable_result_id = snapshot.reusableResultId;
    if (snapshot.quickContext) body.quick_context = { ...snapshot.quickContext };
    body.client_request_id = snapshot.clientRequestId;
    // 结果选择是一次性的：请求体已经捕获后立即消费，避免下一轮普通提问误带旧 ID。
    if (snapshot.reusableResultId) selectedReusableResultId.value = null;
    const headers: any = {
      "Content-Type": "application/json",
    };
    if (config.token) {
      headers["Authorization"] = `Bearer ${config.token}`;
      headers["X-API-Key"] = config.token;
    }
    const response = await fetch("/api/v1/chat/completions", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: abortController.signal,
      credentials: "include"
    });
    if (!response.ok) throw new Error(response.statusText);
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) throw new Error("No body");

    const sseLineParser = createSseLineParser();

    let isChatStreamDone = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const dataLines = sseLineParser.feed(decoder.decode(value, { stream: true }));

      for (const dataStr of dataLines) {
        if (dataStr === "[DONE]") {
          isChatStreamDone = true;
          break;
        }

        // Any SSE data frame means the stream is alive. Keep the fallback
        // prompt hidden while logs, narration, tool events, or answer deltas
        // continue to arrive; it should only appear during a true quiet gap.
        resetStallTimer();

        try {
          const data = JSON.parse(dataStr);
          applyStreamTraceId(agentMsg.value, data);
          if (handleBufferedBodyEvent(data)) {
            // 已进入正文缓冲。
          } else if (data.type === "duplicate_request") {
            flushContentBuffer();
            agentMsg.value.isThinking = false;
            (agentMsg.value as any).status = "duplicate_request";
            agentMsg.value.content = String(data.content || "相同发送请求已提交，请等待原任务完成。\n");
            remoteRunActive.value = true;
            void refreshCurrentRunStatus();
          } else if (data.type === "run_status") {
            flushContentBuffer();
            agentMsg.value.isThinking = false;
            clearStallTimer();
            showStalledPrompt.value = false;
            markOutputCompleted();
            if (data.status === "success") {
              (agentMsg.value as any).status = "success";
            }
          } else if (data.type === "browser_session") {
            const openingGeneration = browserOpenGeneration;
            void attachBrowserSession(
              String(data.session_id || ""),
              data.approval_mode,
              openingGeneration,
            );
          } else if (data.type === "browser_refresh") {
            if (String(data.session_id || "") === String(browserSessionId.value || "")) {
              browserRefreshSignal.value += 1;
            }
          } else if (applyReusableResultStatusEvent(agentMsg.value, data)) {
            // 结果保存/复用状态只更新消息元数据，不改变回答正文。
          } else if (data.type === "log") {
            if (agentMsg.value.isThinking && data.title) {
              agentMsg.value.thinkingText = `正在${data.title}...`;
            }
            addEmbedLogFromStream(agentMsg.value, data);
          } else if (mergeStreamCitations(agentMsg.value, data)) {
            // Citations are merged and de-duplicated by the shared stream normalizer.
          } else if (data.type === "router_log") {
            const agentName = data.selected_agent || "Unknown";
            const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
            const routerId = data.id || "route:target_selection";
            const routerDetails = `已完成目标专家匹配。\n目标专家: ${agentName} ${conf}`.trim();
            addEmbedLogFromStream(agentMsg.value, {
              id: routerId,
              title: "目标专家匹配完成",
              details: routerDetails,
              status: "success",
              isRouter: true,
              category: "router",
              parent_id: data.parent_id || "route:target_config",
              execution_time_ms: data.execution_time_ms ?? null,
            });
          } else if (applyChatBIInsightEvent(agentMsg.value, data) || applyChatBIMetadataGuideEvent(agentMsg.value, data) || applyAgentHandoffEvent(agentMsg.value, data)) {
            // Additive ChatBI evidence event; answer content stays unchanged.
          } else if (dispatchAgentscopeStreamEvent(agentMsg.value, data, addEmbedLogFromStream, messages.value, handleBashEnvEvent)) {
            if (
              (data.type === "permission_required" || (data.type === "retraction" && data.final !== false))
              && thoughtTimer
            ) {
              clearInterval(thoughtTimer);
              thoughtTimer = null;
            }
          } else if (data.type === "meta") {
            if (data.agent_name) {
              agentMsg.value.agentName = data.agent_name;
              if (data.agent_type) agentMsg.value.agentType = data.agent_type;
              if (data.agent_display_name) {
                  agentMsg.value.agentDisplayName = data.agent_display_name;
              }
            }
            if (data.turn_type) {
              agentMsg.value.turnType = data.turn_type;
              if (typeof data.thought_expanded_default === "boolean") {
                agentMsg.value.isThoughtExpanded = data.thought_expanded_default;
              } else {
                agentMsg.value.isThoughtExpanded = config.expandThoughts;
              }
            }
            if (data.prompt_tokens !== undefined) {
              agentMsg.value.prompt_tokens = data.prompt_tokens;
            }
            if (data.completion_tokens !== undefined) {
              agentMsg.value.completion_tokens = data.completion_tokens;
            }
            if (data.has_data_output) {
              agentMsg.value.hasDataOutput = true;
            }
            if (data.permission_notice) {
              agentMsg.value.permissionNotice = data.permission_notice;
            }
            if (data.ltm_applied && data.ltm_data) {
              if (!ltmAlertedInSession.value) {
                activeLtmPreference.value = data.ltm_data;
                ltmAlertedInSession.value = true;
              }
            }
          } else if (data.type === "error") {
            flushContentBuffer();
            agentMsg.value.isThinking = false;
            (agentMsg.value as any).status = "error";
            applyStreamErrorMessage(agentMsg.value, data);
          } else if (data.type === "answer" || data.type === "answer_delta" || data.content) {
            const piece = sanitizeStreamContent(String(data.content || ""));
            if (piece) {
              if (agentMsg.value.isThoughtExpanded && !agentMsg.value.content && !pendingContentBuffer) {
                agentMsg.value.isThoughtExpanded = false;
              }
              queueContentDelta(piece);
              resetStallTimer();
              if (agentMsg.value.isThinking) {
                agentMsg.value.isThinking = false;
                if (thoughtTimer) {
                  clearInterval(thoughtTimer);
                  thoughtTimer = null;
                }
              }
            }
          } else if (data.status === "generating") {
            if (agentMsg.value.content || pendingContentBuffer) {
              agentMsg.value.isThinking = false;
            }
          } else if (data.type === "error" || data.status === "error") {
            flushContentBuffer();
            agentMsg.value.isThinking = false;
            applyStreamErrorMessage(agentMsg.value, data);
          }
          if (!pendingContentBuffer) {
            scrollToBottom();
          }
        } catch (e) {
          console.error("Failed to parse SSE event:", dataStr, e);
        }
      }
      if (isChatStreamDone) {
        try {
          await reader.cancel();
        } catch {
          // ignore
        }
        break;
      }
    }
    for (const dataStr of sseLineParser.flush()) {
      if (dataStr === "[DONE]") continue;
      resetStallTimer();
      try {
        const data = JSON.parse(dataStr);
        applyStreamTraceId(agentMsg.value, data);
        if (handleBufferedBodyEvent(data)) continue;
        if (applyReusableResultStatusEvent(agentMsg.value, data)) continue;
        if (data.type === "log") addEmbedLogFromStream(agentMsg.value, data);
        else if (mergeStreamCitations(agentMsg.value, data)) continue;
        else if (dispatchAgentscopeStreamEvent(agentMsg.value, data, addEmbedLogFromStream, messages.value, handleBashEnvEvent)) continue;
        else if (data.content) queueContentDelta(sanitizeStreamContent(String(data.content)));
      } catch (e) {
        console.error("Failed to parse final SSE event:", dataStr, e);
      }
    }
    flushContentBuffer();
  } catch (e: any) {
    flushContentBuffer();
    if (e.name === "AbortError") {
      agentMsg.value.content += "\n[用户终止]";
    } else if (document.visibilityState === "hidden") {
      console.log("[Stream] Client disconnected in background; awaiting background producer sync on resume.");
    } else {
      agentMsg.value.content += `\n[错误: ${e.message}]`;
    }
  } finally {
    flushContentBuffer();
    isProcessing.value = false;
    void refreshCurrentRunStatus();
    agentMsg.value.isThinking = false;
    void refreshQuota();
    void loadReusableResultAvailability(); // 刷新会话可复用结果入口
    void loadArtifactCounts(); // 刷新产物数量，新生成的产物即时显示角标与按钮
    clearStallTimer();
    clearStalePendingTimer();
    showStalledPrompt.value = false;
    void refreshEmbedContextUsage();
    void refreshEmbedContextCompactions(true);
    if (thoughtTimer) clearInterval(thoughtTimer);
    // Final cleanup: stop any remaining log spinners
    finalizeAllPendingStreamLogs(agentMsg.value);
    scrollToBottom();
  }
};

const BOTTOM_THRESHOLD_PX = 80;

const runScrollToBottom = (force: boolean) => {
  const el = messagesContainer.value;
  if (!el) return;
  if (force) {
    autoScrollEnabled.value = true;
    showNewMessageHint.value = false;
  }
  const { scrollHeight, clientHeight, scrollTop } = el;
  const maxScroll = Math.max(0, scrollHeight - clientHeight);
  const isNearBottom = maxScroll - scrollTop <= BOTTOM_THRESHOLD_PX;
  if (force || isNearBottom || autoScrollEnabled.value) {
    programmaticScrollUntil.value = Date.now() + 120;
    el.scrollTop = scrollHeight;
    showNewMessageHint.value = false;
    queueMicrotask(() => {
      const c = messagesContainer.value;
      if (c) c.scrollTop = c.scrollHeight;
    });
  } else {
    showNewMessageHint.value = true;
  }
};

const scrollToBottom = (force = false) => {
  nextTick(() => runScrollToBottom(force));
};
const handleScroll = (e: Event) => {
    const target = e.target as HTMLDivElement;
    // 1. History Loading Logic (Scroll to Top)
    if (target.scrollTop === 0 && hasMoreHistory.value && !isLoadingHistory.value && messages.value.length > 0) {
       fetchConversationHistory(true);
    }
    const { scrollHeight, clientHeight, scrollTop } = target;
    const maxScroll = Math.max(0, scrollHeight - clientHeight);
    const atBottom = maxScroll - scrollTop <= BOTTOM_THRESHOLD_PX;
    isAtBottom.value = atBottom;

    if (Date.now() < programmaticScrollUntil.value) {
      if (atBottom) {
        showNewMessageHint.value = false;
        autoScrollEnabled.value = true;
      } else if (
        isProcessing.value &&
        maxScroll - scrollTop > BOTTOM_THRESHOLD_PX + 60
      ) {
        // 程序滚底后的中间帧不误判；但若用户已明显离开底部，仍尊重手动阅读
        autoScrollEnabled.value = false;
      }
      return;
    }

    if (atBottom) {
        showNewMessageHint.value = false;
        autoScrollEnabled.value = true;
    } else {
        if (isProcessing.value) {
            autoScrollEnabled.value = false;
        }
    }
};
const fetchUserInfo = async () => {
  try {
    const res = await axios.get('/api/portal/auth/me');
    if (res.data?.data) {
       currentUser.value = res.data.data;
    }
    await refreshQuota();
  } catch (err) {
    console.warn("[Auth] Failed to fetch user info:", err);
  }
};

const onUnmountHandlers = ref<{
  onMessage?: (e: MessageEvent) => void;
  onOnline?: () => void;
  onOffline?: () => void;
  onPortalDrawerKeydown?: (e: KeyboardEvent) => void;
} | null>(null);
// Lifecycle
onMounted(() => {
  console.log("[LifeCycle] EmbedChat mounted. App Version: 2026-01-20-v1");
  window.addEventListener("resize", updateWidth);
  updateWidth();

  const onMessage = (e: MessageEvent) => {
    // Avoid logging raw payload to prevent leaking tokens/config in console
    console.log("[Message] Received postMessage from origin:", e.origin);
    handlePostMessage(e);
  };
  const onOnline = () => {
    connectionStatus.value = "reconnecting";
    setTimeout(() => (connectionStatus.value = "connected"), 1000);
  };
  const onOffline = () => (connectionStatus.value = "disconnected");

  window.addEventListener("message", onMessage);
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  window.addEventListener("fullscreenchange", updateFullScreenStatus);
  // Load Routing Settings
  const savedMulti = localStorage.getItem("yovole_enable_multi_agent");
  if (savedMulti !== null) config.enableMultiAgent = savedMulti === "1";
  const savedShortcuts = localStorage.getItem("yovole_show_shortcuts");
  if (savedShortcuts !== null) config.showShortcuts = savedShortcuts === "1";
  const savedSqlPlan = localStorage.getItem("yovole_enable_sql_plan");
  if (savedSqlPlan !== null) config.enableSqlPlan = savedSqlPlan === "1";
  const savedOverrideModel = localStorage.getItem("yovole_override_model");
  if (savedOverrideModel) config.overrideModel = savedOverrideModel;
  const savedApprovalMode = localStorage.getItem("yovole_approval_mode");
  if (savedApprovalMode === "ask" || savedApprovalMode === "allow" || savedApprovalMode === "deny") {
    config.approvalMode = savedApprovalMode;
  }
  const savedTheme = localStorage.getItem("yovole_embed_theme");
  if (savedTheme) {
    config.theme = savedTheme;
    applyTheme(savedTheme);
  }
  const savedExpandThoughts = localStorage.getItem("yovole_expand_thoughts");
  if (savedExpandThoughts !== null) config.expandThoughts = savedExpandThoughts === "1";
  const savedGroundingBlockMode = localStorage.getItem("yovole_grounding_block_mode");
  if (savedGroundingBlockMode === "strict_buffer" || savedGroundingBlockMode === "stream_with_retraction") {
    config.groundingBlockMode = savedGroundingBlockMode;
  }
  const savedMarkdownTheme = localStorage.getItem("yovole_markdown_theme");
  if (
    savedMarkdownTheme === "default" ||
    savedMarkdownTheme === "minimal" ||
    savedMarkdownTheme === "academic" ||
    savedMarkdownTheme === "apple" ||
    savedMarkdownTheme === "warm" ||
    savedMarkdownTheme === "compact"
  ) {
    config.markdownTheme = savedMarkdownTheme;
  }
  const savedHideMessageBorder = localStorage.getItem("yovole_hide_message_border");
  if (savedHideMessageBorder !== null) {
    config.hideMessageBorder = savedHideMessageBorder === "1";
  }
  const query = new URLSearchParams(window.location.search);
  const queryInstanceId = normalizeEmbedInstanceId(query.get("instance_id"));
  if (queryInstanceId) config.instanceId = queryInstanceId;
  if (query.get("strict_token") === "1") {
    strictTokenValidation.value = true;
    console.log("[LifeCycle] Strict token validation enabled (debug mode).");
  }
  const ticketFromUrl = query.get("ticket");
  if (ticketFromUrl) {
    console.log("[LifeCycle] Ticket found in URL. Exchanging for session token...");
    void (async () => {
      const ok = await exchangeTicketAndApply(ticketFromUrl);
      if (ok) {
        hasPermission.value = true;
        postInitSuccess();
        scheduleUrlTokenInitialization();
        fetchUserInfo();
        fetchAllowedAgents();
        fetchSlashCommands();
      } else {
        hasPermission.value = false;
        postMessageToHost({ type: "INIT_FAILURE", reason: "invalid_ticket" });
      }
    })();
  } else if (query.get("token")) {
    const token = query.get("token")!;
    // 仅设置内存中的 config.token 参与校验；校验通过后再 syncValidatedCredentials，避免脏 URL 覆盖 localStorage
    config.token = token;
    console.log("[LifeCycle] Token found in URL (persist to storage only after validation).");
  }
  if (query.get("agent_id")) {
    const agentId = query.get("agent_id")!;
    urlPinnedAgentKey.value = agentId;
    config.agentId = agentId;
    // 正式锁定在 initChat -> resolveUrlPinnedAgent 成功后完成
  }
  if (query.get("theme")) applyTheme(query.get("theme")!);
  postMessageToHost({ type: "NANZI_WIDGET_READY" });
  if (config.token && !ticketFromUrl) {
    console.log("[LifeCycle] Initializing chat from existing token...");
    scheduleUrlTokenInitialization();
    fetchUserInfo(); // Add explicit user fetch
    fetchAllowedAgents();
    fetchSlashCommands();
  }

  // 切回前台（切回 App 或多标签页）时自动探测并拉取最新会话历史，支持退避轮询直到后台持久化完成
  let visibilitySyncTimer: any = null;
  const clearVisibilitySyncTimer = () => {
    if (visibilitySyncTimer) {
      clearTimeout(visibilitySyncTimer);
      visibilitySyncTimer = null;
    }
  };

  const syncLatestSessionHistory = async (attempt = 1, maxAttempts = 15) => {
    if (!conversationId.value || !hasPermission.value) return;
    try {
      const headers: any = {};
      if (config.token) {
        headers["Authorization"] = `Bearer ${config.token}`;
        headers["X-API-Key"] = config.token;
      }
      const res = await axios.get("/api/v1/chat/history", {
        params: { conversation_id: conversationId.value, page: 1, page_size: 5 },
        headers,
      });
      if (res.data?.data && Array.isArray(res.data.data.items) && res.data.data.items.length > 0) {
        const latestServerItem = res.data.data.items[0];
        if (latestServerItem && latestServerItem.summary) {
          const matchedIndex = messages.value.findIndex(
            m => m.trace_id && m.trace_id === latestServerItem.trace_id && m.role === 'agent'
          );
          if (matchedIndex !== -1) {
            const currentMsg = messages.value[matchedIndex];
            if (currentMsg && (!currentMsg.content || currentMsg.content.length < latestServerItem.summary.length || currentMsg.isThinking)) {
              currentMsg.content = latestServerItem.summary;
              currentMsg.reasoningContent = latestServerItem.reasoning_content ?? currentMsg.reasoningContent;
              currentMsg.processTimeline = hydrateHistoryProcessTimeline(latestServerItem.process_timeline, latestServerItem.reasoning_content);
              currentMsg.isThinking = false;
              if (isProcessing.value) {
                isProcessing.value = false;
              }
              clearVisibilitySyncTimer();
              await nextTick();
              scrollToBottom();
              return;
            }
          } else {
            const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : undefined;
            if (isProcessing.value || (lastMsg && lastMsg.role === 'user')) {
              messages.value.push({
                id: Date.now(),
                trace_id: latestServerItem.trace_id,
                role: 'agent',
                content: latestServerItem.summary,
                reasoningContent: latestServerItem.reasoning_content ?? undefined,
                processTimeline: hydrateHistoryProcessTimeline(latestServerItem.process_timeline, latestServerItem.reasoning_content),
                logs: [],
                isThinking: false,
                feedback: null,
                agentName: latestServerItem.agent_name ?? undefined,
                agentDisplayName: latestServerItem.agent_display_name || (String(latestServerItem.agent_name || '').startsWith('sys_') ? '系统助手' : undefined),
                agentType: latestServerItem.agent_type ?? undefined,
                prompt_tokens: latestServerItem.prompt_tokens ?? undefined,
                completion_tokens: latestServerItem.completion_tokens ?? undefined,
                total_tokens: latestServerItem.total_tokens ?? undefined,
                timestamp: latestServerItem.created_at,
              });
              isProcessing.value = false;
              clearVisibilitySyncTimer();
              await nextTick();
              scrollToBottom();
              return;
            }
          }
        }
      }
    } catch (err) {
      console.warn("[LifeCycle] Failed to sync session on visibility change:", err);
    }

    if (attempt < maxAttempts) {
      const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : undefined;
      const needsSync = isProcessing.value || (lastMsg && (lastMsg.role === 'user' || (lastMsg.role === 'agent' && (lastMsg.isThinking || !lastMsg.content))));
      if (needsSync && document.visibilityState === "visible") {
        clearVisibilitySyncTimer();
        visibilitySyncTimer = setTimeout(() => {
          syncLatestSessionHistory(attempt + 1, maxAttempts);
        }, 1500);
      }
    } else {
      if (isProcessing.value) {
        isProcessing.value = false;
      }
    }
  };

  const onVisibilityChange = () => {
    if (document.visibilityState === "visible") {
      clearVisibilitySyncTimer();
      void refreshCurrentRunStatus();
      syncLatestSessionHistory(1, 15);
    } else {
      stopRemoteRunPolling();
      clearVisibilitySyncTimer();
    }
  };

  document.addEventListener("visibilitychange", onVisibilityChange);

  // Attach cleanup handlers to component instance scope
  (onUnmountHandlers as any).value = { onMessage, onOnline, onOffline, onVisibilityChange, clearVisibilitySyncTimer };
});
onUnmounted(() => {
  cancelPendingUrlTokenInitialization();
  window.removeEventListener("resize", updateWidth);
  window.removeEventListener("fullscreenchange", updateFullScreenStatus);
  const handlers = (onUnmountHandlers as any).value;
  if (handlers?.onMessage) window.removeEventListener("message", handlers.onMessage);
  if (handlers?.onOnline) window.removeEventListener("online", handlers.onOnline);
  if (handlers?.onOffline) window.removeEventListener("offline", handlers.onOffline);
  if (handlers?.onVisibilityChange) document.removeEventListener("visibilitychange", handlers.onVisibilityChange);
  if (handlers?.clearVisibilitySyncTimer) handlers.clearVisibilitySyncTimer();
  disposePortalTimers();
  stopPortalLoadingTips();
  if (thoughtTimer) clearInterval(thoughtTimer);
});
// --- Typewriter Effect ---
const displayedWelcomeMessage = ref("");
let typewriterInterval: any = null;
let typewriterTimeout: any = null;
const startTypewriter = (text: string) => {
  // Clear any existing timers
  clearInterval(typewriterInterval);
  clearTimeout(typewriterTimeout);
  displayedWelcomeMessage.value = "";
  let i = 0;
  let isDeleting = false;
  const typeLoop = () => {
    // Type out
    if (!isDeleting && i <= text.length) {
      displayedWelcomeMessage.value = text.substring(0, i);
      i++;
      if (i > text.length) {
         // Finished typing, wait then delete
         isDeleting = true;
         // Wait 3 seconds before deleting
         typewriterTimeout = setTimeout(typeLoop, 3000);
         return;
      }
      typewriterTimeout = setTimeout(typeLoop, 100); // Typing speed
    }
    // Delete
    else if (isDeleting && i >= 0) {
      displayedWelcomeMessage.value = text.substring(0, i);
      i--;
      if (i < 0) {
        // Finished deleting, restart
        isDeleting = false;
        i = 0;
        // Wait 0.5s before typing again
        typewriterTimeout = setTimeout(typeLoop, 500);
        return;
      }
      typewriterTimeout = setTimeout(typeLoop, 50); // Deleting speed
    }
  };
  typeLoop();
};
// ... existing code ...
// Watch for welcome message changes (init or override)
watch(
  () => config.welcomeMessage,
  (newVal: string) => {
    if (newVal) {
      startTypewriter(newVal);
    }
  },
  { immediate: true }
);
// Cleanup
onUnmounted(() => {
  clearInterval(typewriterInterval);
  clearTimeout(typewriterTimeout);
});
</script>
<style>
@keyframes fade-in-up {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.animate-fade-in-up {
  animation: fade-in-up 0.4s cubic-bezier(0.16, 1, 0.3, 1) both;
}
@keyframes pulse-fast {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.animate-pulse-fast {
  animation: pulse-fast 0.8s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes bounce-dot {
  0%, 100% {
    transform: translateY(0);
    opacity: 0.3;
  }
  50% {
    transform: translateY(-3px);
    opacity: 1;
  }
}
.animate-bounce-dot {
  animation: bounce-dot 0.8s infinite ease-in-out;
  display: inline-block;
}
</style>
<style scoped>
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.2s ease-out;
}
.slide-fade-enter-from {
  opacity: 0;
  transform: translateY(5px);
}
.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}
.bash-banner-fade-enter-active,
.bash-banner-fade-leave-active {
  transition: opacity 0.28s ease-out, transform 0.28s ease-out;
}
.bash-banner-fade-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}
.bash-banner-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.5);
  border-radius: 2px;
}
@keyframes pulse-slow {
  0%,
  100% {
    transform: scale(1);
    opacity: 0.2;
  }
  50% {
    transform: scale(1.15);
    opacity: 0.4;
  }
}
.animate-pulse-slow {
  animation: pulse-slow 3s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
/* Skeleton Loading Animation */
@keyframes pulse-skeleton {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}
.animate-pulse {
  animation: pulse-skeleton 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
/* Enhanced Markdown Styles synchronized from AgentDebug */
:deep(.markdown-body) {
  font-size: 14px;
}
:deep(.markdown-body p) {
  margin-bottom: 1em;
}
:deep(.markdown-body p:last-child) {
  margin-bottom: 0;
}
:deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-weight: 600;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  color: var(--primary-color, #1677ff);
}
:deep(.markdown-body code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 6px;
  font-size: 85%;
}
:deep(.markdown-body pre code) {
  background-color: transparent;
  padding: 0;
  color: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
:deep(.markdown-body pre) {
  margin-top: 1em;
  margin-bottom: 1em;
  padding: 1.25em 1em 1em 1em;
  background-color: #f8fafc;
  color: #0f172a;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: auto;
  position: relative;
  box-shadow: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  line-height: 1.6;
}
.dark :deep(.markdown-body pre) {
  background-color: #0f172a;
  color: #f1f5f9;
  border-color: #1e293b;
}
:deep(.markdown-body pre):before {
  content: "";
  position: absolute;
  top: 10px;
  left: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #ff5f56;
  box-shadow: 16px 0 0 #ffbd2e, 32px 0 0 #27c93f;
  z-index: 1;
}
:deep(.markdown-body ul, .markdown-body ol) {
  padding-left: 1.5em;
  margin-bottom: 1em;
}
:deep(.markdown-body ol) {
  list-style-type: decimal;
}
:deep(.markdown-body ul) {
  list-style-type: disc;
}
:deep(.markdown-body li) {
  margin-bottom: 0.4em;
}
:deep(.markdown-body blockquote) {
  border-left: 4px solid #e5e7eb;
  padding-left: 1rem;
  color: #6b7280;
  margin: 1em 0;
}
.markdown-theme-default {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #e5e7eb;
  --ai-bubble-accent: rgba(22, 119, 255, 0.6);
  --md-table-background: #ffffff;
  --md-table-header: #f8fafc;
  --md-table-text: #334155;
  --md-table-border: #e2e8f0;
  --md-table-cell-border: #eef2f7;
  --md-table-cell-padding: 10px 14px;
}
.markdown-theme-minimal {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #e2e8f0;
  --ai-bubble-accent: #94a3b8;
  --md-table-background: #ffffff;
  --md-table-header: #ffffff;
  --md-table-text: #334155;
  --md-table-border: #f1f5f9;
  --md-table-cell-border: #f8fafc;
  --md-table-cell-padding: 9px 12px;
}
.markdown-theme-academic {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #cbd5e1;
  --ai-bubble-accent: #64748b;
  --md-table-background: #ffffff;
  --md-table-header: #f8fafc;
  --md-table-text: #334155;
  --md-table-border: #cbd5e1;
  --md-table-cell-border: #e2e8f0;
  --md-table-cell-padding: 11px 14px;
}
.markdown-theme-apple {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #d2d2d7;
  --ai-bubble-accent: #86868b;
  --md-table-background: #ffffff;
  --md-table-header: #ffffff;
  --md-table-text: #334155;
  --md-table-border: #f1f5f9;
  --md-table-cell-border: #f8fafc;
  --md-table-cell-padding: 9px 12px;
}
.markdown-theme-warm {
  --ai-bubble-background: #fffdf5;
  --ai-bubble-border: #e4dcd3;
  --ai-bubble-accent: #b58900;
  --md-table-background: #fffdf5;
  --md-table-header: #f5ece2;
  --md-table-text: #586e75;
  --md-table-border: #e4dcd3;
  --md-table-cell-border: #eee8d5;
  --md-table-cell-padding: 10px 14px;
}
.markdown-theme-compact {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #e5e7eb;
  --ai-bubble-accent: #64748b;
  --md-table-background: #ffffff;
  --md-table-header: #f8fafc;
  --md-table-text: #334155;
  --md-table-border: #f1f5f9;
  --md-table-cell-border: #f8fafc;
  --md-table-cell-padding: 6px 10px;
}
.markdown-theme-bauhaus {
  --ai-bubble-background: #ffffff;
  --ai-bubble-border: #111111;
  --ai-bubble-accent: #002fa7;
  --md-table-background: #ffffff;
  --md-table-header: #f3f4f6;
  --md-table-text: #111111;
  --md-table-border: #111111;
  --md-table-cell-border: #d1d5db;
  --md-table-cell-padding: 9px 12px;
}
.markdown-theme-editorial {
  --ai-bubble-background: #fcf9f5;
  --ai-bubble-border: #dcd1c4;
  --ai-bubble-accent: #8c2d19;
  --md-table-background: #fcf9f5;
  --md-table-header: #f7f3ed;
  --md-table-text: #2c2520;
  --md-table-border: #dcd1c4;
  --md-table-cell-border: #e2d7c9;
  --md-table-cell-padding: 11px 14px;
}
.markdown-theme-zen {
  --ai-bubble-background: #f4f6f4;
  --ai-bubble-border: #d8e2d8;
  --ai-bubble-accent: #8fbc8f;
  --md-table-background: #f4f6f4;
  --md-table-header: #e8eee8;
  --md-table-text: #4a5568;
  --md-table-border: #d8e2d8;
  --md-table-cell-border: #d8e2d8;
  --md-table-cell-padding: 10px 14px;
}
.dark .markdown-theme-default,
.dark .markdown-theme-minimal,
.dark .markdown-theme-academic,
.dark .markdown-theme-apple,
.dark .markdown-theme-warm,
.dark .markdown-theme-compact,
.dark .markdown-theme-bauhaus,
.dark .markdown-theme-editorial,
.dark .markdown-theme-zen {
  --ai-bubble-background: #1f2937;
  --md-table-background: #1f2937;
  --md-table-header: #374151;
  --md-table-text: #e5e7eb;
  --md-table-border: #4b5563;
  --md-table-cell-border: #374151;
}
.markdown-theme-default,
.markdown-theme-minimal,
.markdown-theme-academic,
.markdown-theme-apple,
.markdown-theme-warm,
.markdown-theme-compact,
.markdown-theme-bauhaus,
.markdown-theme-editorial,
.markdown-theme-zen {
  background-color: var(--ai-bubble-background) !important;
  border-color: var(--ai-bubble-border) !important;
  border-left-color: var(--ai-bubble-accent) !important;
}
.markdown-theme-default :deep(.markdown-table-scroll),
.markdown-theme-minimal :deep(.markdown-table-scroll),
.markdown-theme-academic :deep(.markdown-table-scroll),
.markdown-theme-apple :deep(.markdown-table-scroll),
.markdown-theme-warm :deep(.markdown-table-scroll),
.markdown-theme-compact :deep(.markdown-table-scroll),
.markdown-theme-bauhaus :deep(.markdown-table-scroll),
.markdown-theme-editorial :deep(.markdown-table-scroll),
.markdown-theme-zen :deep(.markdown-table-scroll) {
  border-color: var(--md-table-border) !important;
  background-color: var(--md-table-background) !important;
}
.markdown-theme-default :deep(table),
.markdown-theme-minimal :deep(table),
.markdown-theme-academic :deep(table),
.markdown-theme-apple :deep(table),
.markdown-theme-warm :deep(table),
.markdown-theme-compact :deep(table),
.markdown-theme-bauhaus :deep(table),
.markdown-theme-editorial :deep(table),
.markdown-theme-zen :deep(table) {
  background-color: var(--md-table-background) !important;
  color: var(--md-table-text) !important;
}
.markdown-theme-default :deep(th),
.markdown-theme-minimal :deep(th),
.markdown-theme-academic :deep(th),
.markdown-theme-apple :deep(th),
.markdown-theme-warm :deep(th),
.markdown-theme-compact :deep(th),
.markdown-theme-bauhaus :deep(th),
.markdown-theme-editorial :deep(th),
.markdown-theme-zen :deep(th) {
  background-color: var(--md-table-header) !important;
  color: var(--md-table-text) !important;
  border-color: var(--md-table-cell-border) !important;
  padding: var(--md-table-cell-padding) !important;
}
.markdown-theme-default :deep(td),
.markdown-theme-minimal :deep(td),
.markdown-theme-academic :deep(td),
.markdown-theme-apple :deep(td),
.markdown-theme-warm :deep(td),
.markdown-theme-compact :deep(td),
.markdown-theme-bauhaus :deep(td),
.markdown-theme-editorial :deep(td),
.markdown-theme-zen :deep(td) {
  color: var(--md-table-text) !important;
  border-color: var(--md-table-cell-border) !important;
  padding: var(--md-table-cell-padding) !important;
}
.message-borderless {
  border-width: 0 !important;
  border-color: transparent !important;
  border-left-color: transparent !important;
  padding-left: 0.75rem !important;
  box-shadow: none !important;
}
:deep(.markdown-body .markdown-table-scroll) {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  margin: 1em 0;
  background: #ffffff;
  -webkit-overflow-scrolling: touch;
}
:deep(.markdown-body table) {
  display: table;
  width: 100%;
  min-width: 680px;
  border: 0;
  border-spacing: 0;
  border-radius: 0;
  margin: 0;
  background: #ffffff;
  font-size: 13px;
  line-height: 1.55;
}
:deep(.markdown-body pre) {
  max-width: 100%;
  overflow-x: auto;
  white-space: pre !important;
  -webkit-overflow-scrolling: touch;
}
/* Scrollbar styles for mobile tables/code */
:deep(.markdown-body pre)::-webkit-scrollbar,
:deep(.markdown-body .markdown-table-scroll)::-webkit-scrollbar {
  height: 4px;
}
:deep(.markdown-body pre)::-webkit-scrollbar-thumb,
:deep(.markdown-body .markdown-table-scroll)::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.1);
  border-radius: 2px;
}
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.3s ease;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
:deep(.markdown-body th) {
  padding: 10px 14px;
  color: #334155;
  background: #f8fafc;
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.4;
  text-align: left !important;
  white-space: nowrap;
  border-bottom: 1px solid #dbe3ec;
}
:deep(.markdown-body td) {
  padding: 10px 14px;
  color: #334155;
  vertical-align: top;
  text-align: left !important;
  border-bottom: 1px solid #eef2f7;
  overflow-wrap: anywhere;
}
:deep(.markdown-body th + th),
:deep(.markdown-body td + td) {
  border-left: 1px solid #f1f5f9;
}
:deep(.markdown-body tbody tr:nth-child(even)) {
  background-color: #fafbfd;
}
:deep(.markdown-body tbody tr:hover) {
  background-color: #f1f5f9;
}
:deep(.markdown-body tbody tr:last-child td) {
  border-bottom: 0;
}
:deep(.markdown-body td:first-child) {
  color: #0f172a;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12.5px;
}
:deep(.markdown-body .markdown-table-toolbar) {
  display: none;
}
@media (max-width: 639px) {
  /* 少列表格：取消桌面端最小宽度，让常见结果一屏完成阅读。 */
  :deep(.markdown-body .markdown-table-scroll) {
    overflow-x: hidden;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-cards) {
    border: 0 !important;
    border-radius: 0;
    background: transparent !important;
  }
  :deep(.markdown-body .markdown-table-toolbar) {
    display: flex;
    justify-content: flex-start;
    padding: 6px 6px 10px;
  }
  :deep(.markdown-body .markdown-table-view-toggle) {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    min-height: 28px;
    padding: 3px 8px;
    border: 1px solid var(--md-table-border);
    border-radius: 0;
    color: var(--md-table-text);
    background: var(--md-table-header);
    font-size: 11px;
    font-weight: 700;
    line-height: 1.35;
    cursor: pointer;
    transition: border-color 0.15s ease, background-color 0.15s ease, transform 0.15s ease;
  }
  :deep(.markdown-body .markdown-table-view-toggle::before) {
    content: '▦';
    font-size: 12px;
    line-height: 1;
  }
  :deep(.markdown-body .markdown-table-view-toggle:hover) {
    border-color: var(--md-table-text);
    background: var(--md-table-background);
  }
  :deep(.markdown-body .markdown-table-view-toggle:focus-visible) {
    outline: 2px solid var(--md-table-text);
    outline-offset: 2px;
  }
  :deep(.markdown-body .markdown-table-view-toggle:active) {
    transform: scale(0.97);
  }
  :deep(.markdown-body table.markdown-table-mobile-compact) {
    width: 100%;
    min-width: 0;
    table-layout: fixed;
    font-size: 12px;
    line-height: 1.45;
  }
  :deep(.markdown-body table.markdown-table-mobile-compact th),
  :deep(.markdown-body table.markdown-table-mobile-compact td) {
    padding: 7px 6px !important;
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  :deep(.markdown-body table.markdown-table-mobile-compact th) {
    font-size: 11px;
  }

  /* 多列表格：按数据行转成纵向信息卡片，字段名来自 th 的 data-label。 */
  :deep(.markdown-body table.markdown-table-mobile-cards) {
    display: block;
    width: 100%;
    min-width: 0;
    font-size: 12px;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards thead) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards tbody),
  :deep(.markdown-body table.markdown-table-mobile-cards tr) {
    display: block;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards tr) {
    margin: 0 0 10px;
    padding: 8px 10px;
    border: 1px solid var(--md-table-border) !important;
    border-radius: 10px;
    background: var(--md-table-background) !important;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards tr:last-child) {
    margin-bottom: 0;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards td) {
    display: grid;
    grid-template-columns: minmax(72px, 34%) minmax(0, 1fr);
    gap: 8px;
    padding: 6px 0 !important;
    border: 0 !important;
    border-bottom: 1px solid var(--md-table-cell-border) !important;
    color: var(--md-table-text) !important;
    font-family: inherit;
    font-size: 12px;
    white-space: normal;
    overflow-wrap: anywhere;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards td:last-child) {
    border-bottom: 0 !important;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards td::before) {
    content: attr(data-label);
    color: var(--md-table-text);
    font-size: 11px;
    font-weight: 700;
  }
  :deep(.markdown-body table.markdown-table-mobile-cards tbody tr:nth-child(even)) {
    background: var(--md-table-background) !important;
  }
  /* 切换到表格后恢复宽表布局，由外层容器提供横向滚动。 */
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table) {
    overflow-x: auto;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards) {
    display: table;
    width: 100%;
    min-width: 680px;
    font-size: 13px;
    line-height: 1.55;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards thead) {
    position: static;
    width: auto;
    height: auto;
    overflow: visible;
    clip: auto;
    clip-path: none;
    white-space: normal;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards tbody) {
    display: table-row-group;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards tr) {
    display: table-row;
    margin: 0;
    padding: 0;
    border: 0 !important;
    border-radius: 0;
    background: var(--md-table-background) !important;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards td) {
    display: table-cell;
    grid-template-columns: none;
    gap: 0;
    padding: var(--md-table-cell-padding) !important;
    border: 0 !important;
    border-bottom: 1px solid var(--md-table-cell-border) !important;
    color: var(--md-table-text) !important;
    font-family: inherit;
    font-size: 13px;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards td + td) {
    border-left: 1px solid var(--md-table-cell-border) !important;
  }
  :deep(.markdown-body .markdown-table-scroll.markdown-table-view-table table.markdown-table-mobile-cards td::before) {
    content: none;
  }
}
/* Highlight.js Color Overrides - Light Theme */
:deep(.hljs-keyword),
:deep(.hljs-selector-tag) {
  color: #d73a49;
}
:deep(.hljs-string) {
  color: #032f62;
}
:deep(.hljs-number) {
  color: #005cc5;
}
:deep(.hljs-type),
:deep(.hljs-built_in) {
  color: #6f42c1;
}
:deep(.hljs-attr),
:deep(.hljs-variable) {
  color: #e36209;
}
:deep(.hljs-comment) {
  color: #6a737d;
  font-style: italic;
}
:deep(.hljs-function) {
  color: #6f42c1;
}
:deep(.hljs-params) {
  color: #24292e;
}
:deep(.hljs-meta) {
  color: #005cc5;
}
:deep(.hljs-operator) {
  color: #d73a49;
}
:deep(.hljs-title) {
  color: #6f42c1;
}
:deep(.hljs-punctuation) {
  color: #24292e;
}
:deep(.markdown-body .code-block-wrapper) {
  position: relative;
  margin-top: 1em;
  margin-bottom: 1em;
}
:deep(.markdown-body .code-block-wrapper pre) {
  margin: 0;
}
:deep(.markdown-body .code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z'/%3E%3C/svg%3E");
  background-size: 16px;
  background-repeat: no-repeat;
  background-position: center;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s;
  z-index: 10;
}
:deep(.markdown-body .code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}
:deep(.markdown-body .code-copy-btn:hover) {
  background-color: #f3f4f6;
  border-color: #d1d5db;
}
:deep(.markdown-body .code-copy-btn.copied) {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2310b981'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M5 13l4 4L19 7'/%3E%3C/svg%3E");
  border-color: #10b981;
}

/* 思维链扫光动效 (已关闭)
.shimmer-thought-card {
  position: relative !important;
  overflow: hidden !important;
}
.shimmer-thought-card::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  transform: translateX(-100%);
  background-image: linear-gradient(
    90deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.45) 20%,
    rgba(255, 255, 255, 0.75) 60%,
    rgba(255, 255, 255, 0) 100%
  );
  animation: shimmer-slide 2s cubic-bezier(0.4, 0, 0.2, 1) infinite;
  pointer-events: none;
  z-index: 5;
}
.dark .shimmer-thought-card::after {
  background-image: linear-gradient(
    90deg,
    rgba(30, 41, 59, 0) 0%,
    rgba(148, 163, 184, 0.08) 20%,
    rgba(148, 163, 184, 0.15) 60%,
    rgba(30, 41, 59, 0) 100%
  );
}
@keyframes shimmer-slide {
  100% {
    transform: translateX(100%);
  }
}
*/

/* 思维链局部温和呼吸动效 */
@keyframes pulse-subtle {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.65;
  }
}

.animate-pulse-subtle {
  animation: pulse-subtle 3s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.custom-table-render :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 11px;
  line-height: 1.5;
  background-color: #ffffff;
}
.dark .custom-table-render :deep(table) {
  background-color: #1f2937;
}
.custom-table-render :deep(th),
.custom-table-render :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 8px;
  text-align: left;
  word-break: break-all;
}
.dark .custom-table-render :deep(th),
.dark .custom-table-render :deep(td) {
  border-color: #374151;
}
.custom-table-render :deep(th) {
  background-color: #f3f4f6;
  font-weight: 700;
  color: #1f2937;
}
.dark .custom-table-render :deep(th) {
  background-color: #374151;
  color: #f9fafb;
}
.custom-table-render :deep(tr:nth-child(even)) {
  background-color: #f9fafb;
}
.dark .custom-table-render :deep(tr:nth-child(even)) {
  background-color: rgba(31, 41, 55, 0.4);
}
.custom-table-render :deep(caption) {
  font-size: 10px;
  color: #6b7280;
  padding: 6px 4px;
  font-weight: 700;
  text-align: left;
  background-color: rgba(243, 244, 246, 0.5);
  border-bottom: 2px solid #e5e7eb;
}
.dark .custom-table-render :deep(caption) {
  color: #9ca3af;
  background-color: rgba(55, 65, 81, 0.5);
  border-color: #4b5563;
}
</style>
