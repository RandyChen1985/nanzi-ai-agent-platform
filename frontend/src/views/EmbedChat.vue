<template>
  <div
    class="flex h-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-sans overflow-hidden relative"
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
      class="flex-1 flex flex-col h-full relative z-10 min-w-0 transition-[margin] duration-300 overflow-hidden"
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
                        <template v-else-if="isMobile && config.routingMode === 'expert' && currentExpertAgent">
                            {{ currentExpertAgent.display_name || currentExpertAgent.name }}
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
                        v-else-if="isMobile && config.routingMode === 'expert' && currentExpertAgent"
                        class="inline-flex items-center px-1.5 py-0.5 rounded-full bg-primary/10 text-primary text-[9px] font-black uppercase tracking-wider shrink-0"
                    >
                        专家
                    </span>
                </div>
                <div class="text-[10px] font-bold uppercase tracking-widest truncate flex items-center gap-1.5 min-w-0">
                    <template v-if="isProcessing">
                        <span class="text-gray-400">正在处理您的请求...</span>
                    </template>
                    <template v-else-if="isMobile && config.routingMode === 'expert' && currentExpertAgent">
                        <span class="text-primary/80 normal-case tracking-normal font-semibold">专家模式</span>
                        <button
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
            <button
                v-if="isMobile || !config.showShortcuts"
                @click="handleHeaderShortcutsClick"
                class="p-2 text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-all"
                :title="isMobile ? '快捷指令' : '显示快捷指令'"
            >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 2L4 14h7l-1 8 10-13h-7l1-7z" />
                </svg>
            </button>
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
        </div>
      </div>

      <ExpertCapsule
          :config="config"
          :current-expert-agent="currentExpertAgent"
          :show-auto-routing-hint="showAutoRoutingHint"
          :show-multi-agent-hint="showMultiAgentHint"
          :multi-agent-hint-message="multiAgentHintMessage"
          :show-expert-switch-hint="showExpertSwitchHint"
          :expert-switch-hint-name="expertSwitchHintName"
          :is-mobile="isMobile"
          @switch-to-auto="switchToAuto"
      />

      <!-- Main Chat Area -->
      <div
        class="flex-1 overflow-y-auto p-3 sm:p-4 space-y-4 sm:space-y-6"
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
        :slash-commands="slashCommands"
        @quick-question="handleQuickQuestion"
        @open-data-portal="openPortalDrawer"
        @select-knowledge-base="openKnowledgePortal"
        @open-workspace="showWorkspaceDrawer = true"
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
                            <span v-if="file.type === 'skill' || file.type === 'knowledge_base' || file.type === 'memory'" class="text-xs font-bold text-white truncate">{{ file.filename }}</span>
                            <template v-else>
                              <span v-if="canPreviewFile(file)" @click="handlePreviewFile(file)" class="text-xs font-bold text-white hover:underline cursor-pointer truncate">{{ file.filename }}</span>
                              <a v-else :href="resolveFileUrl(file.url)" target="_blank" class="text-xs font-bold text-white hover:underline truncate">{{ file.filename }}</a>
                            </template>
                            <span class="text-[9px] text-white/70 font-mono">
                                {{
                                    file.type === 'skill' ? '生态技能' :
                                    file.type === 'knowledge_base' ? '知识库' :
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
                @click="copyMessage(msg.content)"
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
                    d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"
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
              :style="{
                background:
                  'linear-gradient(135deg, var(--primary-color, #1677ff), #9333ea)',
              }"
              @click.stop="showSettings = true; fetchAllowedAgents()"
              title="点击配置主题"
            >
              <svg
                class="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M13 10V3L4 14h7v7l9-11h-7z"
                />
              </svg>
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
          <div class="max-w-[90%]">
            <!-- Agent Name (Smart Status Capsule) -->
            <div class="mb-1 ml-1 flex items-center">
              <div
                class="flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-medium transition-all duration-500 ease-out border"
                :class="msg.agentName
                  ? 'bg-blue-50/80 border-blue-100 text-blue-700 dark:bg-blue-900/20 dark:border-blue-800 dark:text-blue-300 opacity-100 translate-y-0'
                  : 'opacity-0 translate-y-1 bg-transparent border-transparent'"
              >
                <!-- Status Indicator Dot (Removed to reduce visual noise) -->
                <!-- Text -->
                <span>{{ msg.agentDisplayName || msg.agentName }}</span>
                <span class="opacity-70 font-normal">{{ String(msg.agentName || '').startsWith('sys_') ? '· 系统指令' : '· 正在服务' }}</span>
              </div>
            </div>
            <div
              class="px-4 py-3 rounded-2xl rounded-tl-sm shadow-none border border-gray-100 dark:border-gray-700 border-l-4 border-l-primary/60 dark:border-l-primary/40 text-sm leading-relaxed min-h-[46px] transition-all duration-300 relative group/bubble"
              :class="[
                msg.isThinking
                    ? 'bg-slate-50/80 dark:bg-slate-800/80'
                    : 'bg-white dark:bg-gray-800'
              ]"
            >
              <!-- Thinking Process (Collapsible Accordion) -->
              <div v-if="msg.isThinking || (msg.logs && msg.logs.length > 0)" class="mb-2">
                <!-- Accordion Header -->
                <ChatThinkingHeader
                  v-model:expanded="msg.isThoughtExpanded"
                  :is-thinking="msg.isThinking"
                  :title="getThoughtPanelTitle(msg)"
                  :step-count="getDisplayLogs(msg).length"
                  :hidden-step-count="getHiddenLogCount(msg)"
                  :skill-summary="getSkillFlowBadgesForMessage(msg, messages).length > 0 ? summarizeSkillFlowBadges(getSkillFlowBadgesForMessage(msg, messages)) : ''"
                  :duration="msg.thoughtDuration"
                  dark-mode
                />
                <!-- Accordion Body (Logs) -->
                <transition
                  enter-active-class="transition-all duration-300 ease-out"
                  enter-from-class="opacity-0 max-h-0"
                  enter-to-class="opacity-100 max-h-[500px]"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="opacity-100 max-h-[500px]"
                  leave-to-class="opacity-0 max-h-0"
                >
                  <div
                    v-show="msg.isThoughtExpanded"
                    class="overflow-hidden"
                  >
                    <!-- Ecosystem Skills Notice -->
                    <div v-if="getSkillFlowBadgesForMessage(msg, messages).length > 0" class="mt-2 ml-2 pl-4 flex flex-col gap-1.5">
                      <div class="flex items-center space-x-1.5 text-xs text-purple-700 dark:text-purple-400 font-semibold bg-purple-50/50 dark:bg-purple-950/10 border border-purple-100/60 dark:border-purple-900/20 rounded-lg px-3 py-2">
                        <span class="text-[14px]">⚡</span>
                        <span>{{ skillFlowNoticeLabel(getSkillFlowBadgesForMessage(msg, messages)) }}</span>
                        <div class="flex flex-wrap gap-1">
                          <span
                            v-for="skill in getSkillFlowBadgesForMessage(msg, messages)"
                            :key="skill.key"
                            class="px-2 py-0.5 rounded-full bg-purple-100 dark:bg-purple-900/40 text-[10px] font-bold border border-purple-200/50 dark:border-purple-800/30"
                            :title="skill.description"
                          >
                            {{ skill.label }}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div class="relative ml-2 pl-4 py-2 space-y-1.5 border-l border-gray-200 dark:border-gray-700/50">
                      <!-- 骨架屏占位 (响应初期的等待动效) -->
                      <div
                        v-if="msg.isThinking && (!msg.logs || getDisplayLogs(msg).length === 0)"
                        class="space-y-3 py-1.5 animate-pulse"
                      >
                        <div class="flex items-center gap-3">
                          <div class="h-4.5 w-4.5 rounded-full bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                          <div class="h-3 w-28 rounded bg-gray-200 dark:bg-gray-700"></div>
                          <div class="ml-auto h-3 w-8 rounded bg-gray-200 dark:bg-gray-700"></div>
                        </div>
                        <div class="flex items-center gap-3 opacity-50">
                          <div class="h-4.5 w-4.5 rounded-full bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                          <div class="h-3 w-36 rounded bg-gray-200 dark:bg-gray-700"></div>
                          <div class="ml-auto h-3 w-8 rounded bg-gray-200 dark:bg-gray-700"></div>
                        </div>
                      </div>

                      <div
                        v-for="(log, idx) in getDisplayLogs(msg)"
                        :key="idx"
                        class="relative group/log transition-opacity duration-300"
                        :class="{ 'opacity-45 group-hover/log:opacity-80': isDimmedThoughtStep(log, msg.isThinking) }"
                      >
                        <!-- Timeline Numbered Badge (Soft) -->
                        <div class="absolute -left-[23px] top-2 w-[18px] h-[18px] rounded-full flex items-center justify-center text-[9px] font-bold group-hover/log:scale-110 transition-all z-10 select-none ring-4 ring-white dark:ring-gray-800"
                             :class="{
                               'bg-red-50 text-red-500 border border-red-200 dark:bg-red-900/30 dark:border-red-800/50': log.status === 'error',
                               'bg-primary/10 text-primary border border-primary/25 dark:bg-primary/20 dark:border-primary/30': isActiveThoughtStep(log, msg.isThinking),
                               'bg-gray-100 text-gray-500 border border-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700': log.status !== 'error' && !isActiveThoughtStep(log, msg.isThinking),
                               'animate-pulse': log.status === 'pending'
                             }"
                        >
                          {{ Number(idx) + 1 }}
                        </div>
                        <!-- Log Card (Lightweight Row) -->
                        <div
                          class="rounded-lg p-2 text-xs transition-all duration-300 cursor-pointer"
                          :class="{
                             'bg-blue-50/50 dark:bg-blue-900/15 border border-blue-100/80 dark:border-blue-800/40 shadow-sm animate-pulse-subtle': isActiveThoughtStep(log, msg.isThinking),
                             'bg-transparent hover:bg-gray-50 dark:hover:bg-gray-700/30': log.status !== 'error' && !isActiveThoughtStep(log, msg.isThinking),
                             'bg-red-50/30 hover:bg-red-50/50 dark:bg-red-900/10 dark:hover:bg-red-900/20 border border-red-100 dark:border-red-900/30': log.status === 'error'
                          }"
                          @click="log.details ? (log.isExpanded = !log.isExpanded) : null"
                        >
                          <div class="flex items-center justify-between gap-2">
                             <div class="flex items-center gap-2 flex-1 min-w-0"
                                  :class="[
                                    isTechnicalLogStep(log)
                                      ? 'text-[11px] font-normal text-gray-500/90 dark:text-gray-400/90'
                                      : 'font-medium',
                                    {
                                      'text-red-700 dark:text-red-400': log.status === 'error',
                                      'text-gray-800 dark:text-gray-100': isActiveThoughtStep(log, msg.isThinking) && !isTechnicalLogStep(log),
                                      'text-gray-700 dark:text-gray-300': !isActiveThoughtStep(log, msg.isThinking) && log.status !== 'error' && !isTechnicalLogStep(log),
                                    }
                                  ]">
                               <!-- Semantic Icon -->
                               <span class="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center" :class="{ 'animate-pulse': log.status === 'pending' }">
                                 <template v-if="log.status === 'error'">⚠️</template>
                                 <template v-else-if="log.category === 'router'">🧠</template>
                                 <template v-else-if="log.category === 'tool' || log.category === 'sql'">🛠️</template>
                                 <template v-else-if="log.category === 'permission'">🔒</template>
                                 <SparklesIcon v-else class="h-3.5 w-3.5 text-primary" />
                               </span>
                               <!-- Main Text -->
                               <span class="truncate">{{ log.title }}</span>
                               <span
                                 v-if="logHasRowFilterApplied(log)"
                                 class="flex-shrink-0 text-[12px]"
                                 title="已按行级数据权限改写 SQL"
                               >🔒</span>
                               <span
                                 v-if="isActiveThoughtStep(log, msg.isThinking)"
                                 class="inline-flex items-center px-1 sm:px-1.5 py-px sm:py-0.5 rounded text-[8px] sm:text-[9px] font-bold uppercase tracking-wide text-primary bg-primary/10 border border-primary/20 scale-90 sm:scale-100 origin-center animate-pulse"
                               >
                                 进行中
                               </span>
                               <span
                                 v-if="log.status === 'success' && (log.category === 'sql' || (log.title && log.title.toLowerCase().includes('sql')))"
                                 class="text-emerald-500 dark:text-emerald-400 font-bold ml-1 flex-shrink-0 select-none"
                               >
                                 ☑
                               </span>

                             </div>
                             <div class="flex items-center gap-2 flex-shrink-0">
                               <span
                                 v-if="formatLogDuration(log, getDisplayLogs(msg))"
                                 class="w-12 text-right justify-end inline-flex text-[10px] font-mono flex-shrink-0"
                                 :class="getLogDurationColor(log, getDisplayLogs(msg))"
                                 :title="log.status === 'pending' ? '当前步骤已等待时间' : '当前步骤耗时'"
                               >
                                 {{ formatLogDuration(log, getDisplayLogs(msg)) }}
                               </span>
                               <button
                                 v-if="log.details && log.isExpanded"
                                 @click.stop="copyMessage(log.details)"
                                 class="p-1 text-gray-400 hover:text-primary transition-colors"
                                 title="复制详情"
                               >
                                 <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012-2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" /></svg>
                               </button>
                               <svg
                                 v-if="log.details"
                                 class="w-3 h-3 text-gray-400 transition-all duration-200 group-hover/log:opacity-100"
                                 :class="{ 'rotate-180': log.isExpanded, 'opacity-100': log.isExpanded, 'opacity-0': !log.isExpanded }"
                                 fill="none"
                                 stroke="currentColor"
                                 viewBox="0 0 24 24"
                               >
                                 <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                               </svg>
                             </div>
                          </div>                          <!-- Details -->
                          <div v-if="log.details && log.isExpanded" class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700/50">
                            <KnowledgeToolLogDetails
                              v-if="isKnowledgeToolLog(log.details)"
                              :details="log.details"
                              class="mb-1"
                            />
                            <template v-else-if="splitSqlToolLogDetails(log.details)">
                              <div class="space-y-1.5 mb-1">
                                <div class="p-2 bg-gray-900 rounded border border-gray-800 font-mono text-[10px] text-emerald-400 leading-relaxed overflow-x-auto relative group/sql">
                                  <div class="flex justify-between items-center mb-1 text-[9px] text-gray-500 font-sans uppercase tracking-tight">
                                    <span>SQL Query</span>
                                    <div class="flex items-center gap-1">
                                      <button @click.stop="copyMessage(splitSqlToolLogDetails(log.details)!.sqlPart)" class="text-gray-600 hover:text-emerald-400 transition-colors uppercase">Copy</button>
                                      <template v-if="resolveSavableSqlFromLog(log)">
                                        <span class="text-gray-700">|</span>
                                        <button @click.stop="openSaveReportModal(resolveSavableSqlFromLog(log)!, msg)" class="text-gray-600 hover:text-primary transition-colors" title="添加为黄金报表">添加黄金报表</button>
                                      </template>
                                    </div>
                                  </div>
                                  <pre class="whitespace-pre-wrap break-all">{{ splitSqlToolLogDetails(log.details)!.sqlPart }}</pre>
                                </div>
                                <div class="p-2 rounded border font-mono text-[10px] leading-relaxed overflow-x-auto"
                                     :class="splitSqlToolLogDetails(log.details)!.bodyKind === 'error'
                                       ? 'bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-900/40 text-red-700 dark:text-red-300'
                                       : 'bg-gray-50 dark:bg-gray-900/60 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'">
                                  <div class="mb-1 text-[9px] font-sans uppercase tracking-tight"
                                       :class="splitSqlToolLogDetails(log.details)!.bodyKind === 'error' ? 'text-red-500' : 'text-gray-500'">
                                    {{ sqlToolLogBodyLabel(splitSqlToolLogDetails(log.details)!.bodyKind) }}
                                  </div>
                                  <pre class="whitespace-pre-wrap break-all">{{ splitSqlToolLogDetails(log.details)!.bodyPart }}</pre>
                                </div>
                                <pre v-if="splitSqlToolLogDetails(log.details)!.trailingPart" class="font-mono text-[10px] text-amber-600 dark:text-amber-400 whitespace-pre-wrap break-all">{{ splitSqlToolLogDetails(log.details)!.trailingPart }}</pre>
                              </div>
                            </template>
                            <!-- SQL Detection & Pretty Print (legacy / error-only logs) -->
                            <div v-else-if="log.details && isSqlLikeToolLogDetails(log.details)" class="space-y-1.5 mb-1">
                                <div class="p-2 bg-gray-900 rounded border border-gray-800 font-mono text-[10px] text-emerald-400 leading-relaxed overflow-x-auto relative group/sql">
                                    <div class="flex justify-between items-center mb-1 text-[9px] text-gray-500 font-sans uppercase tracking-tight">
                                      <span>SQL Query</span>
                                      <div class="flex items-center gap-1">
                                        <button @click.stop="copyMessage(log.details)" class="text-gray-600 hover:text-emerald-400 transition-colors uppercase">Copy</button>
                                      </div>
                                    </div>
                                    <pre class="whitespace-pre-wrap break-all">{{ log.details }}</pre>
                                </div>
                            </div>
                            <pre v-else class="font-mono text-[10px] text-gray-500 dark:text-gray-400 whitespace-pre-wrap break-all">{{ log.details }}</pre>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </transition>
              </div>
              <!-- Tool Permission Confirmation -->
              <div
                v-if="msg.pendingPermission"
                class="mt-3 rounded-lg border border-amber-200 dark:border-amber-900/50 bg-amber-50/80 dark:bg-amber-900/20 p-3 text-xs"
              >
                <div class="flex items-start gap-2">
                  <div class="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300">
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                    </svg>
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-3">
                      <div class="font-bold text-amber-900 dark:text-amber-100 truncate">
                        {{ msg.pendingPermission.title || '工具调用确认' }}
                      </div>
                      <span
                        class="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase"
                        :class="{
                          'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300': msg.pendingPermission.status === 'pending',
                          'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': msg.pendingPermission.status === 'approved',
                          'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300': msg.pendingPermission.status === 'rejected',
                          'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300': msg.pendingPermission.status === 'error' || msg.pendingPermission.status === 'expired'
                        }"
                      >
                        {{ formatPermissionStatus(msg.pendingPermission.status) }}
                      </span>
                    </div>
                    <div class="mt-1 text-amber-800/80 dark:text-amber-200/80 break-words">
                      {{ msg.pendingPermission.details }}
                    </div>
                    <div
                      v-if="msg.pendingPermission.tool_call?.name"
                      class="mt-2 rounded-md bg-white/70 dark:bg-gray-950/30 border border-amber-100 dark:border-amber-900/40 p-2 font-mono text-[10px] text-gray-600 dark:text-gray-300 overflow-x-auto"
                    >
                      <span>{{ msg.pendingPermission.tool_call.name }}</span>
                      <span v-if="msg.pendingPermission.tool_call.args"> {{ JSON.stringify(msg.pendingPermission.tool_call.args) }}</span>
                    </div>
                    <div v-if="msg.pendingPermission.status === 'pending'" class="mt-3 flex items-center gap-2">
                      <button
                        @click="confirmPendingPermission(msg, true)"
                        :disabled="msg.pendingPermission.isSubmitting"
                        class="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="m5 13 4 4L19 7" />
                        </svg>
                        允许
                      </button>
                      <button
                        @click="confirmPendingPermission(msg, false)"
                        :disabled="msg.pendingPermission.isSubmitting"
                        class="inline-flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-bold text-gray-700 border border-gray-200 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-gray-800 dark:text-gray-200 dark:border-gray-700 dark:hover:bg-gray-700"
                      >
                        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18 18 6M6 6l12 12" />
                        </svg>
                        拒绝
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              <!-- External Tool Execution -->
              <div
                v-if="msg.pendingExternalExecution"
                class="mt-3 rounded-lg border border-sky-200 dark:border-sky-900/50 bg-sky-50/80 dark:bg-sky-900/20 p-3 text-xs"
              >
                <div class="flex items-start gap-2">
                  <div class="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300">
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-3">
                      <div class="font-bold text-sky-900 dark:text-sky-100 truncate">
                        {{ msg.pendingExternalExecution.title || '外部工具执行' }}
                      </div>
                      <span class="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                        {{ formatExternalExecutionStatus(msg.pendingExternalExecution.status) }}
                      </span>
                    </div>
                    <div class="mt-1 text-sky-800/80 dark:text-sky-200/80 break-words">
                      {{ msg.pendingExternalExecution.details }}
                    </div>
                    <div
                      v-if="msg.pendingExternalExecution.tool_call?.name"
                      class="mt-2 rounded-md bg-white/70 dark:bg-gray-950/30 border border-sky-100 dark:border-sky-900/40 p-2 font-mono text-[10px] text-gray-600 dark:text-gray-300 overflow-x-auto"
                    >
                      <span>{{ msg.pendingExternalExecution.tool_call.name }}</span>
                      <span v-if="msg.pendingExternalExecution.tool_call.args"> {{ JSON.stringify(msg.pendingExternalExecution.tool_call.args) }}</span>
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
              <GroundingBlockedCard
                v-if="msg.groundingBlocked"
                class="mt-2"
                :payload="msg.groundingBlocked"
                :disabled="isProcessing"
                @action="(action) => handleGroundingAction(msg.groundingBlocked, action)"
              />
              <!-- Main Content -->
              <div v-if="msg.content && !msg.groundingBlocked" class="relative group/content mt-2">
                <!-- Floating Copy Button (Moved here to avoid overlap) -->
                <button
                  v-if="!msg.datasetNavigation?.groups?.length"
                  @click="copyMessage(msg.content)"
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
                                                                  :content="msg.content"
                                                                  :theme="config.markdownTheme"
                                                                  @quick-question="handleQuickQuestion"
                                                                  @show-citation="(payload) => handleShowCitation(msg, payload.id, payload.anchor)"
                                                                  @open-canvas="handleOpenCanvas"
                                                                />
                                                                <DatasetCapabilityMenu
                                                                  v-else
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
                                                                                                                          <!-- Citation Cards -->
                                                                                                                          <div v-if="msg.citations && msg.citations.length > 0" class="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700/50">
                                                                                                                            <button
                                                                                                                              @click="msg.isCitationsExpanded = !msg.isCitationsExpanded"
                                                                                                                              class="flex items-center space-x-1.5 mb-2 w-full text-left group/cite-head"
                                                                                                                            >
                                                                                                                               <svg class="w-3.5 h-3.5 text-gray-400 group-hover/cite-head:text-primary transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                                                                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                                                                                                                               </svg>
                                                                                                                               <span class="text-[10px] font-bold text-gray-400 uppercase tracking-wider flex-1 group-hover/cite-head:text-gray-600 dark:group-hover/cite-head:text-gray-300 transition-colors">引用来源 ({{ msg.citations.length }})</span>
                                                                                                                               <svg
                                                                                                                                  class="w-3.5 h-3.5 text-gray-400 transform transition-transform duration-200"
                                                                                                                                  :class="{ 'rotate-180': msg.isCitationsExpanded }"
                                                                                                                                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                                                                                                                               >
                                                                                                                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                                                                                                                               </svg>
                                                                                                                            </button>
                                                                                                                              <transition
                                                                                                                                enter-active-class="transition-all duration-300 ease-out"
                                                                                                                                enter-from-class="opacity-0 max-h-0"
                                                                                                                                enter-to-class="opacity-100 max-h-[500px]"
                                                                                                                                leave-active-class="transition-all duration-200 ease-in"
                                                                                                                                leave-from-class="opacity-100 max-h-[500px]"
                                                                                                                                leave-to-class="opacity-0 max-h-0"
                                                                                                                              >
                                                                                                                                <div v-show="msg.isCitationsExpanded" class="overflow-hidden">
                                                                                                                                                                  <div class="flex flex-wrap gap-2 py-1">
                                                                                                                                                                     <template v-for="(cite, cIdx) in msg.citations" :key="cIdx">
                                                                                                                                                                       <div
                                                                                                                                                                          class="citation-chip group/cite relative flex items-center space-x-2 px-2.5 py-1.5 rounded-lg transition-all cursor-pointer overflow-hidden"
                                                                                                                                                                          :class="cite.similarity && cite.similarity < 0.5
                                                                                                                                                                            ? 'bg-amber-50/80 dark:bg-amber-900/20 border border-amber-200/80 dark:border-amber-700/50 hover:border-amber-400/60'
                                                                                                                                                                            : 'bg-gray-50 dark:bg-gray-800/80 border border-gray-100 dark:border-gray-700 hover:border-primary/40 dark:hover:border-primary/40'"
                                                                                                                                                                          @click.stop="openCitationPopover(cite, $event)"
                                                                                                                                                                       >
                                                                                                                                                                          <!-- File Icon -->
                                                                                                                                                                          <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                                                                                                                             <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                                                                                                                                                          </svg>
                                                                                                                                                                          <!-- Label -->
                                                                                                                                                                          <span class="text-[11px] font-medium text-gray-600 dark:text-gray-300 truncate max-w-[120px]" :title="cite.doc_name">
                                                                                                                                                                            {{ cite.doc_name }}
                                                                                                                                                                          </span>
                                                                                                                                                                          <!-- Score -->
                                                                                                                                                                          <span
                                                                                                                                                                            v-if="cite.similarity"
                                                                                                                                                                            class="text-[9px] font-mono px-1 rounded"
                                                                                                                                                                            :class="cite.similarity < 0.5
                                                                                                                                                                              ? 'text-amber-600 dark:text-amber-400 bg-amber-100/80 dark:bg-amber-900/40'
                                                                                                                                                                              : 'text-gray-400 bg-gray-100 dark:bg-gray-700'"
                                                                                                                                                                            :title="cite.similarity < 0.5 ? '相似度较低，请结合原文核对' : undefined"
                                                                                                                                                                          >
                                                                                                                                                                            {{ (cite.similarity * 100).toFixed(0) }}%
                                                                                                                                                                          </span>
                                                                                                                                                                       </div>
                                                                                                                                                                     </template>
                                                                                                                                  </div>
                                                                                                                                </div>
                                                                                                                              </transition>
                                                                                            </div>
                                                            </div>
            <!-- Agent Message Actions (Overlay/Bottom) -->
            <ChatBIDataEvidence v-if="msg.chatbiInsight" :meta="msg.chatbiInsight" />
            <ChatBIMetadataGuide v-if="msg.chatbiMetadataGuide" :guide="msg.chatbiMetadataGuide" @select="handleQuickQuestion" />
            <div class="flex flex-nowrap items-center space-x-2 mt-1.5">
              <button
                @click="copyMessage(msg.content)"
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
                    d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"
                  />
                </svg>
                <span class="hidden sm:inline">复制</span>
              </button>
              <!-- Export Data Button -->
              <button
                v-if="msg.trace_id"
                @click="exportData(msg.trace_id, 'xlsx')"
                class="hidden sm:flex items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                title="导出数据 (Excel)"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <span>导出</span>
              </button>
              <!-- Time -->
              <span v-if="msg.timestamp" class="text-[10px] text-gray-400 dark:text-gray-500 select-none mr-1">{{ formatBubbleTime(msg.timestamp) }}</span>
              <button
                v-if="msg === lastAgentMessage && !isProcessing"
                @click="regenerate"
                class="flex shrink-0 items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                title="重新生成"
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
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
                <span class="hidden sm:inline">重新生成</span>
              </button>
              <button
                v-if="msg.trace_id"
                @click="openEmbedTrace(msg.trace_id)"
                class="hidden md:flex shrink-0 items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                title="链路"
              >
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                <span>链路</span>
              </button>
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
              <!-- 反馈与 ChatBI 扩展操作（靠右对齐） -->
              <div class="flex items-center space-x-1 ml-auto">
                <template v-if="!hideEmbedLikeDislike">
                <button
                  @click="handleFeedback(msg, 'up')"
                  class="rounded transition-colors hover:bg-green-50 dark:hover:bg-green-900/20 text-gray-400 hover:text-green-500"
                  :class="[
                    msg.feedback === 'up' ? 'text-green-500 bg-green-50 dark:bg-green-900/20' : '',
                    windowWidth < 640 ? 'p-2.5' : 'p-1 px-1.5'
                  ]"
                  title="很有帮助"
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
                      d="M14 10h4.708C19.712 10 20.5 10.743 20.5 11.658c0 .354-.05.7-.145 1.03l-1.921 6.641C18.232 20.141 17.514 21 16.5 21H8.5c-1.105 0-2-.895-2-2v-8c0-.55.224-1.05.586-1.414l5-5c.381-.381 1-.381 1.381 0L14 5v5z"
                    />
                  </svg>
                </button>
                <button
                  @click="handleFeedback(msg, 'down')"
                  class="rounded transition-colors hover:bg-red-50 dark:hover:bg-red-900/20 text-gray-400 hover:text-red-500"
                  :class="[
                    msg.feedback === 'down' ? 'text-red-500 bg-red-50 dark:bg-red-900/20' : '',
                    windowWidth < 640 ? 'p-2.5' : 'p-1 px-1.5'
                  ]"
                  title="回答不准确"
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
                  @select="handleQuickQuestion"
                  @action="(action) => handleChatBIResultAction(action, msg)"
                />
                <button
                  v-if="canSaveGoldenReportFromMessage(msg) && checkRole(msg, 'agent') && !msg.isThinking"
                  type="button"
                  @click="handleSaveReportFromMessage(msg)"
                  class="flex shrink-0 items-center space-x-1 text-[10px] text-gray-400 hover:text-primary transition-colors rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                  :class="windowWidth < 640 ? 'p-2.5' : 'px-1.5 py-0.5'"
                  title="将本轮成功查数的 SQL 沉淀为黄金报表"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                  </svg>
                  <span class="hidden sm:inline">添加黄金报表</span>
                </button>
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
    <div class="flex-shrink-0 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-800 relative z-20">
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
        :is-processing="isProcessing"
        :show-shortcuts="!isMobile && config.showShortcuts"
        :slash-commands="slashCommands"
        :allowed-agents="allowedAgents"
        :current-user="currentUser"
        :window-width="windowWidth"
        :approval-mode="config.approvalMode"
        :selected-model="config.overrideModel"
        :available-models="availableModels"
        :active-ltm-preference="activeLtmPreference"
        :agent-id="effectiveEmbedChatAgentId"
        :routing-mode="config.routingMode"
        :expert-agent-id="config.expertAgentId"
        :is-loading-agents="isLoadingAgents"
        @update:approval-mode="(mode) => { config.approvalMode = mode; saveRoutingSettings(); }"
        @update:selected-model="(model) => { config.overrideModel = model; saveRoutingSettings(); }"
        @send="sendMessage"
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
        @select-local-fs="showWorkspaceDrawer = true"
        @select-memory="openMemorySelector"
        @system-command="handleSystemCommand"
        @ignore-ltm="handleIgnoreLtm"
        @dismiss-ltm="activeLtmPreference = null"
      >
      </ChatInput>
    </div>

    <ChatCanvas
      :visible="canvasVisible"
      v-model:pinned="canvasPinned"
      v-model:canvas-width="canvasPinnedWidthReactive"
      :data="canvasData"
      :overlay="canvasFromWorkspace"
      :dock-side="canvasFromWorkspace ? 'left' : 'right'"
      :conversation-id="conversationId"
      @close="closeCanvas"
      @analyze-diff="handleAnalyzeDiff"
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
      :datasets="knowledgeDatasets"
      :active-dataset-ids="activeDatasetIds"
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
                                        <MessageRenderer :content="turn.summary || 'N/A'" :theme="config.markdownTheme" />
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
      style="z-index: 200;"
      title="一键删除分组会话"
      :message="`确定要一键删除“${groupToDelete?.title}”分组下的这 ${groupToDelete?.items?.length || 0} 个会话吗？此操作无法撤销。`"
      type="danger"
      @confirm="confirmDeleteGroup"
      @cancel="showDeleteGroupModal = false"
    />
    <!-- Delete Confirmation Modal -->
    <ConfirmModal
      v-if="showDeleteModal"
      style="z-index: 200;"
      title="删除历史记录"
      message="确定要删除这条对话记录吗？此操作无法撤销。"
      type="danger"
      @confirm="confirmDeleteTrace"
      @cancel="showDeleteModal = false"
    />
    <!-- Delete Command Confirmation Modal -->
    <ConfirmModal
      v-if="showDeleteCommandModal"
      style="z-index: 200;"
      title="删除快捷指令"
      :message="`确定要删除指令 [${commandToDelete?.label}] 吗？`"
      type="danger"
      @confirm="executeDeleteCommand"
      @cancel="showDeleteCommandModal = false"
    />
    <!-- Clear Session Confirmation Modal -->
    <ConfirmModal
      v-if="showConfirmModal"
      style="z-index: 200;"
      title="确定要开始新对话吗？"
      message="当前内容将保存至历史记录。"
      type="primary"
      @confirm="handleConfirmClearSession"
      @cancel="showConfirmModal = false"
    />
    <!-- Settings Modal -->
    <ChatSettings
      v-model:visible="showSettings"
      :config="config"
      :allowed-agents="allowedAgents"
      @set-theme="setTheme"
      @set-color="setColor"
      @mode-change="onModeChange"
      @save-settings="saveRoutingSettings"
      @reset-session="resetSession"
    />
    <!-- Modal: Save Report -->
    <teleport to="body">
    <div
      v-if="showSaveReportModal"
      class="fixed inset-y-0 left-0 z-[250] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      :class="saveReportModalOverlayClass"
      :style="saveReportModalOverlayStyle"
      @click.self="showSaveReportModal = false"
    >
      <div
        class="bg-white dark:bg-gray-800 w-full max-w-lg rounded-2xl shadow-2xl overflow-hidden flex flex-col border border-gray-100 dark:border-gray-700"
      >
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
          <div class="flex items-center space-x-2">
            <div class="p-1.5 bg-primary/10 rounded-lg text-primary">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v2a2 2 0 01-2 2H7a2 2 0 01-2-2V5zM12 9v12m-3-3l3 3 3-3" />
              </svg>
            </div>
	            <h3 class="text-base font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">{{ isEditingReport ? '编辑黄金 SQL 报表' : '沉淀为黄金报表' }}</h3>
          </div>
          <button @click="showSaveReportModal = false" class="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto p-6 space-y-4 custom-scrollbar max-h-[60vh]">
          <div>
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">报表名称 <span class="text-red-500">*</span></label>
            <input
              v-model="saveReportForm.title"
              type="text"
              placeholder="请输入自定义报表名称"
              class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-gray-800 dark:text-gray-200"
            />
          </div>
          <div>
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">报表描述</label>
            <textarea
              v-model="saveReportForm.description"
              rows="2"
              placeholder="说明这个报表适合回答什么业务问题"
              class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-gray-800 dark:text-gray-200 resize-none"
            />
          </div>
          <div>
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">标签</label>
            <input
              v-model="saveReportForm.tags_input"
              type="text"
              placeholder="例如：经营分析, 订单, 月报"
              class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-gray-800 dark:text-gray-200"
            />
          </div>

          <div v-if="saveReportForm.original_query">
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">原始提问</label>
            <div class="px-3 py-2 border border-gray-100 dark:border-gray-800 rounded-xl bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-600 dark:text-gray-400 break-all select-all font-mono leading-relaxed max-h-20 overflow-y-auto">
              {{ saveReportForm.original_query }}
            </div>
          </div>

          <div>
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">SQL 语句预览</label>
            <pre class="px-3 py-2 border border-gray-100 dark:border-gray-800 rounded-xl bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-600 dark:text-gray-400 break-all select-all font-mono leading-relaxed overflow-x-auto max-h-40 overflow-y-auto">{{ saveReportForm.sql_content }}</pre>
            <span class="text-[10px] text-gray-400 mt-1 block">提示：系统将自动反查关联的数据集与数据源以保证直连执行时能够顺利通过权限安全校验。</span>
          </div>
          <div
            v-if="saveReportForm.mode === 'param_sql'"
            class="p-3 rounded-xl border border-blue-100 dark:border-blue-900/40 bg-blue-50/50 dark:bg-blue-950/20 text-xs text-blue-700 dark:text-blue-300 leading-relaxed"
          >
            已识别到固定日期条件，将保存为动态日期报表。后续运行时可选择今天、昨天、最近 7 天、本月或自定义日期范围。
          </div>
        </div>

        <!-- Footer -->
        <div class="px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex justify-end space-x-3 bg-gray-50/50 dark:bg-gray-800/50">
          <button
            @click="showSaveReportModal = false"
            class="px-4 py-2 text-xs font-bold text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border border-gray-200 dark:border-gray-700 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          >
            取消
          </button>
          <button
            @click="submitSaveReport"
            :disabled="isSavingReport"
            class="px-4 py-2 text-xs font-bold text-white bg-primary rounded-xl hover:bg-primary-hover active:bg-primary-active disabled:opacity-50 transition-colors flex items-center space-x-1.5"
          >
            <svg v-if="isSavingReport" class="w-3.5 h-3.5 animate-spin text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
	            <span>{{ isEditingReport ? '保存修改' : '沉淀报表' }}</span>
          </button>
        </div>
      </div>
    </div>
    </teleport>

    <!-- Modal: Run Saved Report -->
    <teleport to="body">
    <div
      v-if="showReportRunModal"
      class="fixed inset-y-0 left-0 z-[250] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      :class="saveReportModalOverlayClass"
      :style="saveReportModalOverlayStyle"
      @click.self="showReportRunModal = false"
    >
      <div class="bg-white dark:bg-gray-800 w-full max-w-md rounded-2xl shadow-2xl overflow-hidden border border-gray-100 dark:border-gray-700">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50">
          <div>
            <h3 class="text-base font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">运行黄金报表</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate max-w-[18rem]">{{ pendingSavedReport?.title }}</p>
          </div>
          <button @click="showReportRunModal = false" class="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="p-6 space-y-4">
          <div v-if="!savedReportUsesMonthRange(pendingSavedReport)">
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">日期范围</label>
            <select
              v-model="reportRunForm.dateRange"
              class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-gray-800 dark:text-gray-200"
            >
              <option value="today">今天</option>
              <option value="yesterday">昨天</option>
              <option value="last_7_days">最近 7 天</option>
              <option value="month_start_to_today">本月截至今天</option>
              <option value="custom_range">自定义日期</option>
            </select>
          </div>
          <div v-if="reportRunForm.dateRange === 'custom_range'" class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">开始日期</label>
              <input v-model="reportRunForm.startDate" type="date" class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm text-gray-800 dark:text-gray-200" />
            </div>
            <div>
              <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">结束日期</label>
              <input v-model="reportRunForm.endDate" type="date" class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm text-gray-800 dark:text-gray-200" />
            </div>
          </div>
          <div v-if="savedReportUsesMonthRange(pendingSavedReport)">
            <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">月份范围</label>
            <select
              v-model="reportRunForm.monthRange"
              class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-gray-800 dark:text-gray-200"
            >
              <option value="last_6_completed_months">最近 6 个完整月</option>
              <option value="year_start_to_current_month">本年截至本月</option>
              <option value="custom_month_range">自定义月份</option>
            </select>
          </div>
          <div v-if="savedReportUsesMonthRange(pendingSavedReport) && reportRunForm.monthRange === 'custom_month_range'" class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">开始月份</label>
              <input v-model="reportRunForm.startMonth" type="month" class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm text-gray-800 dark:text-gray-200" />
            </div>
            <div>
              <label class="block text-xs font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">结束月份</label>
              <input v-model="reportRunForm.endMonth" type="month" class="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-950 text-sm text-gray-800 dark:text-gray-200" />
            </div>
          </div>
          <div class="flex items-center justify-between gap-3 p-3 rounded-xl border border-blue-100 dark:border-blue-900/40 bg-blue-50/40 dark:bg-blue-950/20">
            <span>
              <span class="block text-sm font-bold text-gray-800 dark:text-gray-100">执行并分析</span>
              <span class="block text-xs text-gray-500 dark:text-gray-400 mt-0.5">执行完成后将自动让 ChatBI 解读结果</span>
            </span>
          </div>
          <div class="rounded-xl border border-gray-100 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-950/40 overflow-hidden min-h-[10.5rem]">
            <div class="px-3 py-2 flex items-center justify-between border-b border-gray-100 dark:border-gray-800">
              <span class="text-xs font-black text-gray-600 dark:text-gray-300">实际执行 SQL</span>
              <span
                class="text-[10px] font-bold px-2 py-0.5 rounded"
                :class="isPreviewingSavedReport ? 'bg-gray-100 text-gray-500' : reportRunPreview?.permission_status === 'denied' ? 'bg-red-50 text-red-600' : reportRunPreview?.permission_status === 'allowed' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'"
              >
                {{ isPreviewingSavedReport ? '预检中' : reportRunPreview?.permission_status === 'denied' ? '无权限' : reportRunPreview?.permission_status === 'allowed' ? '可运行' : '待校验' }}
              </span>
            </div>
            <div v-if="isPreviewingSavedReport" class="px-3 py-4 text-xs text-gray-400">正在生成运行预览...</div>
            <pre v-else class="max-h-44 overflow-auto px-3 py-2 text-[11px] font-mono leading-relaxed text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{{ reportRunPreview?.rendered_sql || pendingSavedReport?.sql_content || '' }}</pre>
            <p v-if="reportRunPreview?.permission_message" class="px-3 pb-3 text-[11px] text-red-500">{{ reportRunPreview.permission_message }}</p>
          </div>
        </div>
        <div class="px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex justify-end space-x-3 bg-gray-50/50 dark:bg-gray-800/50">
          <button @click="showReportRunModal = false" class="px-4 py-2 text-xs font-bold text-gray-500 border border-gray-200 dark:border-gray-700 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
            取消
          </button>
          <button
            @click="executeSavedReportWithOptions()"
            :disabled="isPreviewingSavedReport || !reportRunPreview || reportRunPreview?.can_run === false"
            class="px-4 py-2 text-xs font-bold text-white bg-primary rounded-xl hover:bg-primary-hover active:bg-primary-active disabled:opacity-50 transition-colors"
          >
            开始运行
          </button>
        </div>
      </div>
    </div>
    </teleport>

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
              🚀 欢迎使用<b>南孜·智能体平台</b>。本系统是一个集成多模型能力的 🤖 AI 助手，旨在通过自然语言交互，帮助您高效完成<b>通用咨询问答</b>、📊 数据查询分析、📚 私有文档检索及 ⚙️ 复杂业务流程处理。
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
                        <svg v-else @click="copyToClipboard('帮我写一个关于机房节能降耗的宣传文案。', 'help_chat')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                        <svg v-else @click="copyToClipboard('查询上海区域所有机房的剩余机柜数', 'help_bi')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                        <svg v-else @click="copyToClipboard('本周的 CS 和 ops 工单有哪些？', 'help_kb')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                        <svg v-else @click="copyToClipboard('查询最近 1 小时的网络延迟报警，并写一封邮件告知运维团队。', 'help_task')" class="w-3 h-3 text-gray-400 opacity-0 group-hover/item:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                      <svg v-else @click="copyToClipboard('查看 B7 机房最近的所有高压报警，并给出可能的根本原因分析。', 'help_scenario_fault')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                      <svg v-else @click="copyToClipboard('统计各机房昨天的监控指标数据量，并进行分析。', 'help_scenario_inspect')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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
                      <svg v-else @click="copyToClipboard('查找 2024 年 Q4 季度的所有变更审批记录，汇总为表格。', 'help_scenario_audit')" class="w-3.5 h-3.5 text-gray-400 opacity-0 group-hover/scenario:opacity-100 cursor-copy" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" /></svg>
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

    <!-- Model Call Stats Modal -->
    <div
      v-if="showStatsModal"
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      @click.self="showStatsModal = false"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700 flex flex-col max-h-[85%]">
        <!-- Header -->
        <div class="px-4 py-3.5 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gray-50 dark:bg-gray-800/50 shrink-0">
          <div class="flex items-center space-x-2">
            <svg class="w-4 h-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24" :style="{ color: 'var(--primary-color, #1677ff)' }">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2" />
            </svg>
            <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200">大模型调用明细指标</h3>
          </div>
          <button @click="showStatsModal = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors cursor-pointer">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Body -->
        <div class="p-4 overflow-y-auto space-y-4 flex-1">
          <!-- Loading skeleton -->
          <div v-if="loadingStats" class="space-y-3 py-6">
            <div class="h-4 bg-gray-100 dark:bg-gray-700 rounded w-2/3 animate-pulse"></div>
            <div class="space-y-2">
              <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse"></div>
              <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse w-5/6"></div>
              <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse w-4/5"></div>
            </div>
          </div>

          <!-- Empty state -->
          <div v-else-if="currentStats.length === 0" class="text-center py-8 text-gray-400 dark:text-gray-500 text-sm">
            <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            暂无此消息的大模型调用明细记录
          </div>

          <!-- Content -->
          <div v-else class="space-y-4">
            <!-- Summary stats -->
            <div class="grid grid-cols-4 gap-2 text-center">
              <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
                <div class="text-[10px] text-gray-400 dark:text-gray-500">调用次数</div>
                <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalCalls }}</div>
              </div>
              <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
                <div class="text-[10px] text-gray-400 dark:text-gray-500">总耗时</div>
                <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalDuration }}s</div>
              </div>
              <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
                <div class="text-[10px] text-gray-400 dark:text-gray-500">总输入</div>
                <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalIn }}</div>
              </div>
              <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
                <div class="text-[10px] text-gray-400 dark:text-gray-500">总输出</div>
                <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalOut }}</div>
              </div>
            </div>

            <!-- Detailed logs list -->
            <div class="space-y-3">
              <div
                v-for="(stat, index) in currentStats"
                :key="index"
                class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/50 rounded-xl p-3 space-y-2 transition-all hover:shadow-sm"
              >
                <!-- Log header -->
                <div class="flex items-start justify-between">
                  <div class="flex flex-col">
                    <div class="flex items-center space-x-1.5">
                      <span class="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold text-white rounded bg-primary/80 shrink-0" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }">
                        #{{ stat.call_index }}
                      </span>
                      <span class="text-xs font-bold text-gray-700 dark:text-gray-300 max-w-[150px] truncate" :title="stat.agent_name">
                        {{ stat.agent_name }}
                      </span>
                    </div>
                    <span v-if="stat.timestamp" class="text-[9px] text-gray-400 dark:text-gray-500 mt-1 font-mono">
                      调用时间: {{ formatModelCallTime(stat.timestamp) }}
                    </span>
                  </div>
                  <span class="text-[10px] text-gray-400 dark:text-gray-500 font-mono text-right shrink-0">
                    {{ (stat.elapsed_ms / 1000).toFixed(2) }}s ({{ stat.elapsed_ms }}ms)
                  </span>
                </div>

                <!-- Log parameters -->
                <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                  <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                    <span class="text-gray-400">大模型名称:</span>
                    <span class="font-medium text-gray-700 dark:text-gray-300 font-mono text-[11px] truncate max-w-[130px]" :title="stat.model_name">
                      {{ stat.model_name }}
                    </span>
                  </div>
                  <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                    <span class="text-gray-400">输入信息数:</span>
                    <span class="font-medium text-gray-700 dark:text-gray-300">
                      {{ stat.input_message_count }}
                    </span>
                  </div>
                  <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                    <span class="text-gray-400">输入 Token:</span>
                    <span class="font-medium text-gray-700 dark:text-gray-300 font-mono">
                      {{ stat.input_tokens }}
                      <span v-if="stat.cache_input_tokens > 0" class="text-[10px] text-green-500 font-normal ml-0.5" :title="'命中上下文缓存 Token: ' + stat.cache_input_tokens">
                        (hit:{{ stat.cache_input_tokens }}, {{ ((stat.cache_input_tokens / stat.input_tokens) * 100).toFixed(0) }}%)
                      </span>
                    </span>
                  </div>
                  <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                    <span class="text-gray-400">输出 Token:</span>
                    <span class="font-medium text-gray-700 dark:text-gray-300 font-mono">
                      {{ stat.output_tokens }}
                    </span>
                  </div>
                </div>

                <!-- Tool Calls -->
                <div class="pt-1 text-[11px] space-y-1.5">
                  <div class="flex items-center space-x-1">
                    <span class="text-gray-400 shrink-0">工具调用:</span>
                    <span
                      v-if="stat.has_tool_calls && stat.tool_names && stat.tool_names.length > 0"
                      class="inline-flex flex-wrap gap-1"
                    >
                      <span
                        v-for="tName in stat.tool_names"
                        :key="tName"
                        class="bg-blue-50 dark:bg-blue-900/30 text-blue-500 border border-blue-100/50 dark:border-blue-800/30 px-1 py-0.5 rounded text-[9px] font-mono"
                      >
                        {{ tName }}
                      </span>
                    </span>
                    <span v-else-if="stat.has_tools_bound" class="text-gray-400 italic">
                      无（已绑定工具但未调用）
                    </span>
                    <span v-else class="text-gray-400 italic">
                      无（未绑定工具）
                    </span>
                  </div>
                  <!-- Tool Call Arguments Details -->
                  <div v-if="stat.tool_calls && stat.tool_calls.length > 0" class="bg-gray-100/60 dark:bg-gray-950/40 p-2 rounded-lg text-[10px] font-mono text-gray-600 dark:text-gray-400 border border-gray-100 dark:border-gray-800 space-y-1 max-h-[100px] overflow-y-auto">
                    <div v-for="(call, cIdx) in stat.tool_calls" :key="cIdx" class="break-all whitespace-pre-wrap">
                      <span class="text-blue-500 dark:text-blue-400 font-bold">{{ call.name }}</span>(<span class="text-gray-600 dark:text-gray-400">{{ formatToolArgs(call.arguments) }}</span>)
                    </div>
                  </div>
                </div>

                <!-- Thoughts and Output Text Expansion Panel -->
                <div v-if="stat.reasoning_content || stat.response_text" class="pt-1 border-t border-gray-100/50 dark:border-gray-700/20">
                  <button
                    @click="toggleStatExpand(stat.call_index)"
                    class="text-[10px] text-primary dark:text-blue-400 hover:underline flex items-center space-x-1 font-bold focus:outline-none cursor-pointer"
                  >
                    <span>{{ expandedStats[stat.call_index] ? '收起思考与输出' : '展开思考与输出' }}</span>
                    <svg class="w-3 h-3 transform transition-transform" :class="expandedStats[stat.call_index] ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  <div v-if="expandedStats[stat.call_index]" class="mt-2 space-y-2 text-[10px] font-mono">
                    <!-- Reasoning/Thought -->
                    <div v-if="stat.reasoning_content" class="bg-amber-50/40 dark:bg-amber-950/10 border border-amber-100/50 dark:border-amber-900/20 p-2 rounded-lg text-amber-800 dark:text-amber-300">
                      <div class="font-bold text-[9px] uppercase text-amber-500 mb-1">思考过程 (Thought)</div>
                      <div class="whitespace-pre-wrap leading-relaxed">{{ stat.reasoning_content }}</div>
                    </div>
                    <!-- Final text output -->
                    <div v-if="stat.response_text" class="bg-gray-100/80 dark:bg-gray-950/60 border border-gray-200/50 dark:border-gray-800/40 p-2 rounded-lg text-gray-700 dark:text-gray-300">
                      <div class="font-bold text-[9px] uppercase text-gray-400 mb-1">大模型输出 (Output)</div>
                      <div class="whitespace-pre-wrap leading-relaxed max-h-[200px] overflow-y-auto break-all">{{ stat.response_text }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <DatasetPortalDrawer
      v-model="showPortalDrawer"
      v-model:keep-open-on-question="portalKeepOpenOnQuestion"
      v-model:pinned="portalPinned"
      v-model:drawer-width="portalDrawerWidthReactive"
      :payload="portalNavigationPayload"
      :initial-loading="portalLoading && !portalNavigationPayload"
      :background-refreshing="portalBackgroundRefreshing"
      :focus-saved-report-request="savedReportFocusRequest"
      @quick-question="handlePortalQuickQuestion"
      @record-question-click="(payload) => recordDatasetMenuQuestionClick(portalNavigationPayload, payload)"
	      @clear-question-click="(payload) => clearDatasetMenuQuestionClick(portalNavigationPayload, payload)"
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
    </div>
</template>
<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, nextTick, watch, computed, triggerRef } from "vue";
import { useRouter } from "vue-router";
import axios from "@/utils/axios";
import { finalizeConversation } from "@/utils/conversationFinalize";
import { cancelConversationRun } from "@/utils/cancelConversationRun";
import { createConversationId } from "@/utils/conversationId";
import { useToast } from "../composables/useToast";
import { useTokenQuota } from "../composables/useTokenQuota";
import { buildQuotaStatusMarkdown } from "@/utils/quotaDisplay";
import { useDatasetPortal } from "@/composables/useDatasetPortal";
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

import { SparklesIcon } from "@heroicons/vue/24/outline";
import { useBranding } from "@/composables/useBranding";

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
import GroundingBlockedCard from "@/components/GroundingBlockedCard.vue";
import DatasetCapabilityMenu from "@/components/chatbi/DatasetCapabilityMenu.vue";
import DatasetPortalDrawer from "@/components/chatbi/DatasetPortalDrawer.vue";
import ChatBIDataEvidence from "@/components/chatbi/ChatBIDataEvidence.vue";
import ChatBIContinueAnalysis from "@/components/chatbi/ChatBIContinueAnalysis.vue";
import ChatBIMonitorDialog from "@/components/chatbi/ChatBIMonitorDialog.vue";
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
import RagPreviewDrawer from "@/components/RagPreviewDrawer.vue";
import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";
import ConfirmModal from "@/components/ConfirmModal.vue";
import ExpertCapsule from "@/components/embed/ExpertCapsule.vue";
import ChatSettings from "@/components/embed/ChatSettings.vue";
import ChatCanvas from "@/components/embed/ChatCanvas.vue";
import ChatThinkingHeader from "@/components/chat/ChatThinkingHeader.vue";
import ChatInput from "@/components/embed/ChatInput.vue";
import WelcomeDashboard from "@/components/embed/WelcomeDashboard.vue";
import WorkspaceBrowserDrawer from "@/components/embed/WorkspaceBrowserDrawer.vue";
import MemoryBrowserDrawer from "@/components/embed/MemoryBrowserDrawer.vue";
import SkillCreatedBanner from "@/components/chat/SkillCreatedBanner.vue";
import { parseSkillCreatedMarker, type SkillCreatedInfo } from "@/utils/skillCreated";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import { isImageAttachment } from "@/utils/attachmentImages";
import { isDirectRenderableUrl, resolvePublicUploadsPreviewUrl } from "@/utils/workspaceFilePreview";
import TraceLogViewer from "@/components/TraceLogViewer.vue";
import { sanitizeStreamContent } from "@/utils/streamContentSanitize";
import { normalizeAgentSwitchCommand } from "@/utils/agentSwitchCommands";
import { createSseLineParser } from "@/utils/chartRenderer";
import { modelApi, type AIModel } from "@/api/model";
import {
  filterLogsForTurn,
  countHiddenLogs,
  isActiveThoughtStep,
  isDimmedThoughtStep,
  type TurnType,
} from "@/utils/turnLogDisplay";
import { getEmbedThoughtSummaryTitle } from "@/utils/embedThoughtStages";
import {
  buildSkillFlowBadges,
  skillFlowNoticeLabel,
  summarizeSkillFlowBadges,
  type SkillFlowBadge,
} from "@/utils/skillFlowBadges";
import {
  splitSqlToolLogDetails,
  isSqlLikeToolLogDetails,
  sqlToolLogBodyLabel,
  resolveSavableSqlFromLog,
  canSaveGoldenReportFromMessage,
  resolveSavableSqlFromMessage,
  logHasRowFilterApplied,
} from "@/utils/toolLogDisplay";
import {
  deriveSavedReportDescription,
  deriveSavedReportTagsInput,
  deriveSavedReportTitle,
  parseRequirementAnalysisFromMessage,
} from "@/utils/savedReportDefaults";
import {
  buildSavedReportRunParams,
  detectSavedReportDateTemplate,
  extractSavedReportExecuteErrorMessage,
  parseSavedReportTags,
  renderSavedReportDataToMarkdown,
  todayDateString,
  todayMonthString,
} from "@/composables/chat/useSavedReportWorkflow";
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas";
import {
  USER_MESSAGE_CONTEXT_DIVIDER,
  splitUserMessageContent,
  useChatAttachments,
} from "@/composables/chat/useChatAttachments";
import { groupChatHistoryByDate } from "@/composables/chat/useChatHistoryGroups";
import KnowledgeToolLogDetails from "@/components/KnowledgeToolLogDetails.vue";
import { isKnowledgeToolLog } from "@/utils/knowledgeToolLog";
import {
  applyStreamTraceId,
  dispatchAgentscopeStreamEvent,
  formatExternalExecutionStatus,
  formatPermissionStatus,
  isLiveThoughtStepTimer,
  resolveStreamLogDurationMs,
  finalizeAllPendingStreamLogs,
  markStalePendingStreamLogs,
  mergeStreamCitations,
  resumeExternalExecutionStream,
  type PendingExternalExecution,
  type PendingToolPermission,
  type GroundingBlockedAction,
  type GroundingBlockedPayload,
} from "@/utils/agentscopeSseHandlers";
// --- Types ---
interface LogEntry {
  id: number | string;
  name?: string;
  title: string;
  details: string;
  status: "pending" | "success" | "error";
  isExpanded: boolean;
  isRouter?: boolean;
  category?: 'router' | 'sql' | 'knowledge' | 'tool' | 'intent' | 'permission' | 'external' | 'model' | 'agent' | 'context' | 'default';
  execution_time_ms?: number | null;
  elapsed_time_ms?: number | null;
  started_at?: number | null;
  rowFilterApplied?: boolean;
}

interface SavedReportPayload {
  id: string;
  title: string;
  sql_content: string;
  mode?: string;
  sql_template?: string;
  params_schema?: any[];
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
  files?: ChatFile[];
  logs?: LogEntry[];
  citations?: any[];
  isThinking?: boolean;
  isThoughtExpanded?: boolean;
  isCitationsExpanded?: boolean;
  thoughtStartTime?: number;
  thoughtDuration?: string;
  thinkingText?: string;
  agentName?: string;
  agentDisplayName?: string;
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
  _hasSilentlyRefreshed?: boolean;
}
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

const formatModelCallTime = (isoStr: string): string => {
  try {
    const date = new Date(isoStr);
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return `${y}-${m}-${d} ${hours}:${minutes}:${seconds}`;
  } catch (e) {
    return isoStr || "";
  }
};


function getDisplayLogs(msg: Message) {
  return filterLogsForTurn(msg.logs, msg.turnType);
}

function getThoughtPanelTitle(msg: Message) {
  const baseTitle = getEmbedThoughtSummaryTitle({
    logs: getDisplayLogs(msg),
    isThinking: msg.isThinking,
    thinkingText: msg.thinkingText,
    turnType: msg.turnType,
  });

  if (msg.isThinking && !msg.isThoughtExpanded) {
    const logs = getDisplayLogs(msg);
    const activeLog = logs.find(log => isActiveThoughtStep(log, msg.isThinking));
    if (activeLog && activeLog.title) {
      return `${baseTitle} · 正在进行: ${activeLog.title}`;
    }
  }
  return baseTitle;
}

function getHiddenLogCount(msg: Message) {
  return countHiddenLogs(msg.logs, getDisplayLogs(msg));
}

function getSkillFlowBadgesForMessage(msg: Message, allMessages: Message[]): SkillFlowBadge[] {
  if (msg.role !== 'agent') return [];
  const idx = allMessages.findIndex(m => m.id === msg.id);
  if (idx <= 0) return [];
  let files: ChatFile[] = [];
  for (let i = idx - 1; i >= 0; i--) {
    const prev = allMessages[i];
    if (prev.role === 'user') {
      files = prev.files || [];
      break;
    }
  }
  return buildSkillFlowBadges(files, msg.logs || []);
}

const formatDurationMs = (durationMs?: number | null): string => {
  if (durationMs === undefined || durationMs === null || Number.isNaN(durationMs)) return "";
  if (durationMs < 100) return "<0.1s";
  return `${(durationMs / 1000).toFixed(1)}s`;
};

const formatLogDuration = (log: LogEntry, allLogs?: LogEntry[]): string => {
  if (log.execution_time_ms !== undefined && log.execution_time_ms !== null) {
    return formatDurationMs(log.execution_time_ms);
  }
  if (log.elapsed_time_ms !== undefined && log.elapsed_time_ms !== null) {
    return formatDurationMs(log.elapsed_time_ms);
  }
  if (isLiveThoughtStepTimer(log, allLogs || []) && log.started_at) {
    return formatDurationMs(Date.now() - log.started_at);
  }
  return "";
};

const isTechnicalLogStep = (log: LogEntry): boolean => {
  return log.category === 'tool' || log.category === 'sql' || log.category === 'permission';
};

const getLogDurationColor = (log: LogEntry, allLogs?: LogEntry[]): string => {
  let ms = 0;
  if (log.execution_time_ms !== undefined && log.execution_time_ms !== null) {
    ms = log.execution_time_ms;
  } else if (log.elapsed_time_ms !== undefined && log.elapsed_time_ms !== null) {
    ms = log.elapsed_time_ms;
  } else if (isLiveThoughtStepTimer(log, allLogs || []) && log.started_at) {
    ms = Date.now() - log.started_at;
  } else {
    return "text-gray-400 dark:text-gray-500";
  }

  if (ms < 500) {
    return "text-emerald-500/80 dark:text-emerald-400/70";
  } else if (ms < 2000) {
    return "text-gray-400 dark:text-gray-500";
  } else {
    return "text-amber-500 dark:text-amber-400 font-medium";
  }
};

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
    return allowedAgents.value.find(a => a.id === config.expertAgentId);
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
    readStoredBoolean("embed_workspace_pinned", false),
);
watch(workspacePinned, (val) => {
  localStorage.setItem("embed_workspace_pinned", val ? "1" : "0");
});

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

const formatToolArgs = (args: any): string => {
  if (!args) return "{}";
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args);
  } catch (e) {
    return String(args);
  }
};

watch(showStatsModal, (newVal) => {
  if (!newVal) {
    expandedStats.value = {};
  }
});

const statsSummary = computed(() => {
  let totalCalls = currentStats.value.length;
  let totalDuration = currentStats.value.reduce((acc, cur) => acc + (cur.elapsed_ms || 0), 0);
  let totalIn = currentStats.value.reduce((acc, cur) => acc + (cur.input_tokens || 0), 0);
  let totalOut = currentStats.value.reduce((acc, cur) => acc + (cur.output_tokens || 0), 0);
  return {
    totalCalls,
    totalDuration: (totalDuration / 1000).toFixed(2),
    totalIn,
    totalOut
  };
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

const { appendAttachmentContext } = useChatAttachments({
  buildKnowledgeBaseAttachmentHint,
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

/** 发给 API 的消息：仅当前轮 user 保留附件，历史轮次只传纯文字 */
const buildOutboundMessages = () => {
  const sendable = messages.value.filter((m) => !m.isThinking && (m.content || m.files));
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
      content: m.role === "user" ? resolveReqContent(m) : (m.content || ""),
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
  enableGrounding: false,
  expandThoughts: true, // 思考过程默认展示开关
  markdownTheme: "default" as "default" | "minimal" | "academic" | "apple" | "warm" | "compact",
});
const showAutoRoutingHint = ref(false);
const showMultiAgentHint = ref(false);
const showExpertSwitchHint = ref(false);
const expertSwitchHintName = ref("");
const showConfirmModal = ref(false);
const multiAgentHintMessage = ref("");
let expertSwitchHintTimer: ReturnType<typeof setTimeout> | null = null;

const saveRoutingSettings = () => {
    localStorage.setItem("yovole_routing_mode", config.routingMode);
    localStorage.setItem("yovole_expert_agent_id", config.expertAgentId || "");
    localStorage.setItem("yovole_enable_multi_agent", config.enableMultiAgent ? "1" : "0");
    localStorage.setItem("yovole_show_shortcuts", config.showShortcuts ? "1" : "0");
    localStorage.setItem("yovole_enable_sql_plan", config.enableSqlPlan ? "1" : "0");
    localStorage.setItem("yovole_override_model", config.overrideModel || "");
    localStorage.setItem("yovole_approval_mode", config.approvalMode || "ask");
    localStorage.setItem("yovole_embed_theme", config.theme || "light");
    localStorage.setItem("yovole_expand_thoughts", config.expandThoughts ? "1" : "0");
    localStorage.setItem("yovole_markdown_theme", config.markdownTheme || "default");
};
const triggerMultiAgentHint = (enabled: boolean) => {
    multiAgentHintMessage.value = enabled ? "已开启多智能体协同模式" : "已切换为单智能体模式";
    showExpertSwitchHint.value = false;
    showMultiAgentHint.value = true;
    setTimeout(() => {
        showMultiAgentHint.value = false;
    }, 3000);
};
const switchToAuto = () => {
    config.routingMode = "auto";
    saveRoutingSettings();
    showExpertSwitchHint.value = false;
    showAutoRoutingHint.value = true;
    setTimeout(() => {
        showAutoRoutingHint.value = false;
    }, 3000);
};
const switchToExpert = (agentId: string) => {
    config.expertAgentId = agentId;
    config.routingMode = "expert";
    saveRoutingSettings();
    const agent = allowedAgents.value.find((a: any) => a.id === agentId);
    expertSwitchHintName.value = agent?.display_name || agent?.name || "专家";
    showAutoRoutingHint.value = false;
    showMultiAgentHint.value = false;
    showExpertSwitchHint.value = true;
    if (expertSwitchHintTimer) clearTimeout(expertSwitchHintTimer);
    expertSwitchHintTimer = setTimeout(() => {
        showExpertSwitchHint.value = false;
        expertSwitchHintTimer = null;
    }, 3000);
};
const onModeChange = (mode: string) => {
    saveRoutingSettings();
    if (mode === "auto") {
        showAutoRoutingHint.value = true;
        setTimeout(() => {
            showAutoRoutingHint.value = false;
        }, 3000);
    }
};
watch(() => config.enableMultiAgent, (newVal, oldVal) => {
    if (newVal !== oldVal) {
        triggerMultiAgentHint(newVal);
    }
});
const conversationId = ref("");
let requestedConversationId = "";

const embedAuthHeaders = (): Record<string, string> | undefined => {
  if (!config.token) return undefined;
  return {
    Authorization: `Bearer ${config.token}`,
    "X-API-Key": config.token,
  };
};

const finalizeConversationInBackground = (cid: string) => {
  void finalizeConversation(cid, embedAuthHeaders());
};

const updateActiveConversationOnServer = async (cid: string) => {
  if (!config.token) return;
  try {
    await axios.post("/api/v1/chat/active", {
      conversation_id: cid
    }, {
      headers: embedAuthHeaders()
    });
  } catch (e: any) {
    console.warn("[ActiveConv] Failed to update active conversation on server:", e);
  }
};

const generateNewConversation = () => {
  const previousId = conversationId.value;
  if (previousId) {
    finalizeConversationInBackground(previousId);
  }
  // 工作台等入口通过 INIT_CONFIG 写入的 resume id，新会话时必须清掉，
  // 否则随后 initChat() 会再次强制切回旧会话并重载历史。
  requestedConversationId = "";
  conversationId.value = createConversationId();
  localStorage.setItem("yovole_embed_conv_id", conversationId.value);
  updateActiveConversationOnServer(conversationId.value);
};
// Mention State (Moved to ChatInput)
// const showMentionList = ref(false); // Removed
// const mentionKeyword = ref(""); // Removed
// const mentionPosition = reactive({ top: 0, left: 0 }); // Removed
const fetchUserMarkdownThemePreference = async () => {
    try {
        const res = await axios.get("/api/portal/portal-prefs");
        if (res.data?.data?.markdown_theme) {
            config.markdownTheme = res.data.data.markdown_theme;
            localStorage.setItem("user_has_custom_theme", "true");
        } else {
            localStorage.removeItem("user_has_custom_theme");
        }
    } catch (error) {
        console.warn("Failed to fetch user markdown theme preference from Redis", error);
    }
};

const allowedAgents = ref<any[]>([]);
const hasFetchedAgents = ref(false);
const isLoadingAgents = ref(false);
const fetchAllowedAgents = async (force = false) => {
    if (hasFetchedAgents.value && !force) return;
    isLoadingAgents.value = true;
    try {
        // 先获取用户在后端持久化的排版样式偏好
        await fetchUserMarkdownThemePreference();

        const res = await axios.get("/api/portal/agents/allowed");
        if (res.data) {
            allowedAgents.value = res.data; // Already filtered by backend
            hasFetchedAgents.value = true;
            
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
    }
}, { immediate: true });

const handleSwitchMode = (agent: any) => {
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

const lockToDataQueryAgentForDatasetMenu = async (): Promise<boolean> => {
    await fetchAllowedAgents();
    const dataQueryAgent = findUniqueDataQueryAgent();
    if (!dataQueryAgent) return false;
    handleSwitchMode(dataQueryAgent);
    return true;
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
    "正在分析任务...",
    "正在组织回答...",
  ];
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
      const stepIndex = ticks / 30;
      if (stepIndex < THINKING_MESSAGES.length) {
        msg.thinkingText = THINKING_MESSAGES[stepIndex];
      } else {
        msg.thinkingText = "任务处理中，请稍候...";
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
  { id: "sys_clear", command: "/new", label: "💬 新会话", sort_order: -40 },
  { id: "sys_history", command: "/history", label: "🕒 历史", sort_order: -39 },
  { id: DATASET_PORTAL_SYSTEM_COMMAND_ID, command: DATASET_PORTAL_SLASH_COMMAND, label: "📊 数据门户", sort_order: -35 },
  { id: KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID, command: KNOWLEDGE_PORTAL_SLASH_COMMAND, label: "📚 知识库中心", sort_order: -34.5 },
  { id: WORKSPACE_SYSTEM_COMMAND_ID, command: WORKSPACE_SLASH_COMMAND, label: "💻 工作空间", sort_order: -34 },
  { id: "sys_quota", command: "/quota", label: "📊 我的额度", sort_order: -18 },
  { id: "sys_settings", command: "/settings", label: "⚙️ 设置", sort_order: -15 },
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
  try {
    await navigator.clipboard.writeText(text);
    if (id) {
      copiedId.value = id;
      setTimeout(() => {
        if (copiedId.value === id) copiedId.value = "";
      }, 2000);
    }
  } catch (err) {
    console.error('Failed to copy:', err);
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
    conversationId.value = item.conversation_id;
    localStorage.setItem("yovole_embed_conv_id", item.conversation_id);
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
const startEdit = (msg: Message) => {
  editingMsgId.value = msg.id;
  editContent.value = splitUserMessageContent(msg.content).userPart;
};
const cancelEdit = () => {
  editingMsgId.value = null;
  editContent.value = "";
};
const saveAndResend = async () => {
  if (editingMsgId.value === null) return;
  const msgIndex = messages.value.findIndex(m => m.id === editingMsgId.value);
  if (msgIndex === -1) return;
  const originalMsg = messages.value[msgIndex];
  if (!originalMsg) return;
  const newContent = editContent.value.trim();
  if (!newContent) return;
  // Truncate history: keep up to this message
  messages.value = messages.value.slice(0, msgIndex);
  // Reset
  editingMsgId.value = null;
  editContent.value = "";
  // Send
  userInput.value = newContent;
  if (originalMsg.files?.length && chatInputRef.value) {
    chatInputRef.value.uploadedFiles = [...originalMsg.files];
  }
  await sendMessage();
};

const handleAnalyzeDiff = async (question: string) => {
  canvasVisible.value = false;
  userInput.value = question;
  await nextTick();
  sendMessage();
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
});

// 画布钉住状态（钉住后侧边固定，不遮挡对话）
const canvasPinned = ref(false);

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


// 黄金报表暂存状态
const showSaveReportModal = ref(false);
const isSavingReport = ref(false);
const isEditingReport = ref(false);
const editingReportId = ref<string | null>(null);
const saveReportForm = ref({
  title: '',
  description: '',
  sql_content: '',
  dataset_id: null as number | null,
  data_source: 'default_clickhouse',
  original_query: '',
  mode: 'static_sql',
  sql_template: '',
  params_schema: [] as any[],
  default_params: {} as Record<string, any>,
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

  saveReportForm.value = {
    title: deriveSavedReportTitle(requirementIntent, originalQuery),
    description: deriveSavedReportDescription(requirementIntent, originalQuery),
    sql_content: cleanSql,
    dataset_id: null,
    data_source: 'default_clickhouse',
    original_query: originalQuery,
    mode: detectedTemplate ? 'param_sql' : 'static_sql',
    sql_template: detectedTemplate?.sql_template || '',
    params_schema: detectedTemplate?.params_schema || [],
    default_params: detectedTemplate?.default_params || {},
    analysis_mode: 'auto',
    tags_input: deriveSavedReportTagsInput(requirementIntent, originalQuery),
  };
  showSaveReportModal.value = true;
};

const openEditReportModal = (report: any) => {
  isEditingReport.value = true;
  editingReportId.value = report.id;
  saveReportForm.value = {
    title: report.title || '',
    description: report.description || '',
    sql_content: report.sql_content || '',
    dataset_id: report.dataset_id ?? null,
    data_source: report.data_source || 'default_clickhouse',
    original_query: report.original_query || '',
    mode: report.mode || 'static_sql',
    sql_template: report.sql_template || '',
    params_schema: report.params_schema || [],
    default_params: report.default_params || {},
    analysis_mode: 'auto',
    tags_input: Array.isArray(report.tags) ? report.tags.join(', ') : '',
  };
  showSaveReportModal.value = true;
};

const submitSaveReport = async () => {
  if (!saveReportForm.value.title.trim()) {
    showToast("请输入报表标题", "error");
    return;
  }
  isSavingReport.value = true;
  try {
    const payload = {
      title: saveReportForm.value.title.trim(),
      description: saveReportForm.value.description?.trim() || undefined,
      sql_content: saveReportForm.value.sql_content,
      dataset_id: saveReportForm.value.dataset_id,
      data_source: saveReportForm.value.data_source,
      original_query: saveReportForm.value.original_query,
      mode: saveReportForm.value.mode,
      sql_template: saveReportForm.value.sql_template || undefined,
      params_schema: saveReportForm.value.params_schema,
      default_params: saveReportForm.value.default_params,
      analysis_mode: saveReportForm.value.analysis_mode,
      tags: parseSavedReportTags(saveReportForm.value.tags_input),
    };
    if (isEditingReport.value && editingReportId.value) {
      await axios.put(`/api/portal/saved-reports/${editingReportId.value}`, payload);
      showToast("报表修改成功", "success");
    } else {
      await axios.post("/api/portal/saved-reports", payload);
      showToast("报表暂存成功！您可以在我的数据门户中查看。", "success");
    }
    showSaveReportModal.value = false;
    isEditingReport.value = false;
    editingReportId.value = null;
  } catch (error: any) {
    console.error("Failed to save report:", error);
    const detail = error.response?.data?.detail || "暂存失败，请重试";
    showToast(typeof detail === 'object' ? JSON.stringify(detail) : detail, "error");
  } finally {
    isSavingReport.value = false;
  }
};

const savedReportNeedsRunOptions = (report: SavedReportPayload) => {
  return report.mode === 'param_sql' && Array.isArray(report.params_schema) && report.params_schema.length > 0;
};

const savedReportUsesMonthRange = (report?: SavedReportPayload | null) => {
  return Boolean(report?.params_schema?.some((item: any) => item?.type === 'month_range' || item?.name === 'month_range'));
};

let suppressSavedReportRunPreviewWatch = false;

const prepareSavedReportRunForm = (report: SavedReportPayload) => {
  suppressSavedReportRunPreviewWatch = true;
  const defaults = report.default_params || {};
  reportRunForm.value = {
    dateRange: String(defaults.date_range || 'month_start_to_today'),
    startDate: String(defaults.start_date || todayDateString()),
    endDate: String(defaults.end_date || todayDateString()),
    monthRange: String(defaults.month_range || 'last_6_completed_months'),
    startMonth: String(defaults.start_month || todayMonthString()),
    endMonth: String(defaults.end_month || todayMonthString()),
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
    closePortalDrawer({ keepDataQueryAgent: true });
  }

  isProcessing.value = true;

  messages.value.push({
    id: Date.now(),
    role: "user",
    content: `📌 执行黄金 SQL 报表: ${report.title}`,
    timestamp: new Date().toISOString(),
  });

  const agentMsg = ref<Message>({
    id: Date.now() + 1,
    role: "agent",
    agentName: "chat-bi",
    agentDisplayName: "数据智能助手",
    content: "",
    isThinking: true,
    thinkingText: "正在进行免模型极速直连安全执行，请稍候...",
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
    const shouldAutoAnalyze = true;
    const res = await axios.post(`/api/portal/saved-reports/${report.id}/execute`, {
      params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value),
      analysis_mode: 'auto',
    }, {
      params: { conversation_id: conversationId.value }
    });

    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";

    let resultMarkdown = "";
    let detailsText = "";

    if (res.data && res.data.data !== undefined) {
      const execResult = res.data.data;
      resultMarkdown = renderSavedReportDataToMarkdown(execResult);
      detailsText = `${report.sql_content}\n--- 结果 ---\n${typeof execResult === 'object' ? JSON.stringify(execResult, null, 2) : String(execResult)}`;
      agentMsg.value.permissionNotice = execResult?.permission_notice;
    } else {
      resultMarkdown = "执行结果为空。";
      detailsText = `${report.sql_content}\n--- 结果 ---\n无`;
    }

    // 直连成功后输出表格，并在结尾拼接“深度可视化分析一下”快捷按钮，方便用户手动点击触发大模型分析流程
    agentMsg.value.content = `### 📊 黄金报表「${report.title}」执行结果：\n\n${resultMarkdown}\n\n---\n- [🙋 深度可视化分析一下](quick:深度可视化分析一下)`;

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
    if (shouldAutoAnalyze) {
      pendingSavedReport.value = null;
      setTimeout(() => {
        handleQuickQuestion("请基于刚才黄金报表结果做业务解读，指出关键结论、异常点和后续建议。");
      }, 0);
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
const toggleShortcuts = () => {
  config.showShortcuts = !config.showShortcuts;
  saveRoutingSettings();
  if (config.showShortcuts) {
    fetchSlashCommands();
  }
};

const handleHeaderShortcutsClick = () => {
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
  };
  setTimeout(() => openPortalDrawer(), 0);
};
const handlePostMessage = (event: MessageEvent) => {
  // Security check logic here in production
  const data = event.data;
  if (
    data.instance_id &&
    config.instanceId &&
    data.instance_id !== config.instanceId
  ) {
    return; // Ignore messages for other instances
  }
  switch (data.type) {
    case "INIT_CONFIG":
      console.log("Received INIT_CONFIG payload:", JSON.stringify({ ...data, token: data.token ? "***" : "MISSING", api_key: data.api_key ? "***" : "MISSING", apikey: data.apikey ? "***" : "MISSING" }));
      const incomingToken = data.token || data.api_key || data.apikey;
      if (incomingToken) {
        config.token = incomingToken;
        hasPermission.value = true; // Reset permission state to try again
        // Configure axios defaults immediately
        axios.defaults.headers.common["Authorization"] = `Bearer ${incomingToken}`;
        axios.defaults.headers.common["X-API-Key"] = incomingToken;
        if (data.agent_id) {
          const agentId = String(data.agent_id);
          config.agentId = agentId;
          // 工作台 / 门户指定助手时，切到专家模式并选中该助手
          switchToExpert(agentId);
          if (!data.conversation_id) {
            // 未指定会话：新开对话，避免仍停在上一会话内容
            messages.value = [];
            generateNewConversation();
            // 钉住本次新会话，供紧随其后的 initChat 加载空历史（而非服务端旧 active）
            requestedConversationId = conversationId.value;
          }
        }
        if (data.conversation_id) {
          requestedConversationId = String(data.conversation_id);
          conversationId.value = requestedConversationId;
          localStorage.setItem("yovole_embed_conv_id", requestedConversationId);
        } else if (!data.agent_id) {
          // 父页已取消会话钉选时，勿保留陈旧 resume id
          requestedConversationId = "";
        }
        if (data.instance_id) config.instanceId = data.instance_id;
        if (data.theme) applyTheme(data.theme, data.styleVars);
        if (data.welcome_message_override)
          config.welcomeMessage = data.welcome_message_override;
        if (data.user_avatar) config.userAvatar = data.user_avatar;
              // Handle injected user info
              if (data.user_info || data.user) {
                const u = data.user_info || data.user;
                currentUser.value = u;
                // Inject identity into context so AI knows who the user is
                injectedContext.value = {
                  ...injectedContext.value,
                  user_name: u.real_name || u.user_name,
                  user_role: u.role === 'admin' ? '系统管理员' : '普通用户',
                  user_id: u.user_id
                };
              }
        // Store initial page info if provided
        if (data.page_info) {
          injectedContext.value = { ...injectedContext.value, ...data.page_info };
        }
        openSavedReportFromHost(data.open_saved_report);
        if (data.portal_question?.query) {
          setTimeout(
            () => handlePortalQuickQuestion(
              String(data.portal_question.query),
              data.portal_question.action === "fill" ? "fill" : "send",
            ),
            0,
          );
        }
        postMessageToHost({ type: "INIT_SUCCESS" });
        initChat(); // Only init if token exists
      } else {
        console.warn("INIT_CONFIG received but no token/api_key found in payload!");
      }
      break;
    case "OPEN_SAVED_REPORT":
      openSavedReportFromHost(data.open_saved_report);
      break;
    case "SYNC_STATE":
      // Host syncing page state (e.g. current URL, selected item)
      if (data.payload) {
        injectedContext.value = { ...injectedContext.value, ...data.payload };
        // Also update user info if present in payload
        if (data.payload.user_info || data.payload.user) {
          const u = data.payload.user_info || data.payload.user;
          currentUser.value = u;
          injectedContext.value.user_name = u.real_name || u.user_name;
          injectedContext.value.user_role = u.role === 'admin' ? '系统管理员' : '普通用户';
        }
        console.log("State synced from host:", data.payload);
      }
      break;
    case "UPDATE_CONTEXT":
      const newContext = data.payload || data.context;
      if (newContext) {
        injectedContext.value = { ...injectedContext.value, ...newContext };
        // Also update user info if present
        if (newContext.user_info || newContext.user) {
          const u = newContext.user_info || newContext.user;
          currentUser.value = u;
          injectedContext.value.user_name = u.real_name || u.user_name;
          injectedContext.value.user_role = u.role === 'admin' ? '系统管理员' : '普通用户';
        }
        console.log("Context updated:", injectedContext.value);
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
      resetSession(data.new_token);
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
const resetSession = (newToken?: string) => {
  messages.value = [];
  config.enableGrounding = false;
  generateNewConversation();
  if (newToken) {
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

/** 仅在服务端校验通过后写入，避免 URL 里陈旧的 ?token= 覆盖刚登录写入的 api_key（父页 Chat.vue postMessage 会读 localStorage）。 */
const syncValidatedCredentials = (apiKey: string) => {
  config.token = apiKey;
  localStorage.setItem("yovole_token", apiKey);
  localStorage.setItem("api_key", apiKey);
  axios.defaults.headers.common["Authorization"] = `Bearer ${apiKey}`;
  axios.defaults.headers.common["X-API-Key"] = apiKey;
};

const validateToken = async (): Promise<boolean> => {
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

  const candidates: string[] = [];
  const add = (t?: string | null) => {
    const s = t?.trim();
    if (s && !candidates.includes(s)) candidates.push(s);
  };
  add(config.token);
  add(localStorage.getItem("api_key"));
  add(localStorage.getItem("yovole_token"));

  const authHeaders = (token: string) => ({
    Authorization: `Bearer ${token}`,
    "X-API-Key": token,
  });

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
const initChat = async () => {
  isInitialLoading.value = true;
  try {
    // 1. Validate Token First (The only blocking step)
    const isValid = await validateToken();
    if (!isValid) {
      hasPermission.value = false;
      isInitialLoading.value = false;
      return;
    }
    hasPermission.value = true;
    // 2. Clear skeleton as soon as auth is confirmed
    isInitialLoading.value = false;
    // 3. Set default welcome message if not provided
    if (!config.welcomeMessage) {
      const displayName = accountInfo.value?.real_name || accountInfo.value?.user_name || "";
      const greeting = displayName ? `您好，${displayName}！` : "您好！";
      config.welcomeMessage = `${greeting}我是南孜智能体，很高兴为您服务。`;
    }
    // 4. Background tasks (non-blocking)
    Promise.all([fetchModels(), fetchAccountInfo(), fetchSlashCommands()]).catch(err => {
      console.warn("[Init] Non-critical background loading failed:", err.message);
    });
    // 5. Preload Agents & Validate Expert Mode
    fetchAllowedAgents().then(() => {
        if (config.routingMode === 'expert' && config.expertAgentId) {
            const isValid = allowedAgents.value.some(a => a.id === config.expertAgentId);
            if (!isValid) {
                console.warn("[Init] Saved expert agent invalid/unauthorized. Downgrading to Auto.");
                switchToAuto();
            }
        }
    }).catch(e => console.warn("Failed to preload agents", e));
    // 6. Workbench/host explicit resume wins; otherwise fetch the active conversation.
    let loadedCid = false;
    if (requestedConversationId) {
      conversationId.value = requestedConversationId;
      localStorage.setItem("yovole_embed_conv_id", requestedConversationId);
      updateActiveConversationOnServer(requestedConversationId);
      loadedCid = true;
    } else {
      try {
        const activeRes = await axios.get("/api/v1/chat/active", {
          headers: embedAuthHeaders()
        });
        if (activeRes.data?.status === "success" && activeRes.data?.data?.conversation_id) {
          conversationId.value = activeRes.data.data.conversation_id;
          localStorage.setItem("yovole_embed_conv_id", conversationId.value);
          loadedCid = true;
        }
      } catch (e: any) {
        console.warn("[Init] Failed to fetch active conversation from server:", e);
      }
    }

    if (!loadedCid) {
      if (!conversationId.value) {
        generateNewConversation();
      } else {
        updateActiveConversationOnServer(conversationId.value);
      }
    }

    // 7. Load history if exists
    if (conversationId.value) {
      fetchConversationHistory(false).catch(e => console.error("[Init] History load failed:", e));
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
const fetchConversationHistory = async (isLoadMore = false) => {
  if (!conversationId.value) return;
  if (isLoadMore && !hasMoreHistory.value) return;
  if (isLoadingHistory.value) return;
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
          params: { conversation_id: conversationId.value, page: page, page_size: HISTORY_LIMIT },
          headers
      }
    );
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
          if (item.summary) {
              newHistoryBatch.push({
                  id: Date.now() + idx * 2 + 1 + offset,
                  trace_id: item.trace_id,
                  role: 'agent',
                  content: item.summary,
                  logs: [],
                  isThinking: false,
                  feedback: null,
                  agentName: item.agent_name ?? undefined,
                  agentDisplayName: item.agent_display_name ?? undefined,
                  prompt_tokens: item.prompt_tokens ?? undefined,
                  completion_tokens: item.completion_tokens ?? undefined,
                  total_tokens: item.total_tokens ?? undefined,
                  timestamp: item.created_at
              });
          }
      });
      if (newHistoryBatch.length > 0) {
        if (isLoadMore) {
           // Prepend to messages (remove existing "History Start" separator if it exists)
           messages.value = [...newHistoryBatch, ...messages.value.filter(m => m.role !== 'system' || m.content !== '以上是历史会话，可以重置会话清除')];
           // Restore scroll position
           await nextTick();
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
    isLoadingHistory.value = false;
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
    await openPortalDrawer();
    return true;
  }
  if (isWorkspaceSlashCommand(normalizedCmd)) {
    userInput.value = "";
    showWorkspaceDrawer.value = true;
    return true;
  }
  if (isKnowledgePortalSlashCommand(normalizedCmd)) {
    userInput.value = "";
    await openKnowledgePortal();
    return true;
  }
  if (normalizedCmd === "/switch_to_auto" || normalizedCmd === "/switch_agent_auto") {
    userInput.value = "";
    switchToAuto();
    showToast("已切换为自动路由模式", "success");
    return true;
  }
  if (normalizedCmd.startsWith("/switch_agent_expert?agent_id=")) {
    userInput.value = "";
    const agentId = normalizedCmd.split("?agent_id=")[1];
    if (agentId) {
      switchToExpert(agentId);
      showToast("已切换到指定智能体", "success");
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
    case "/new":
    case "/clear": // legacy alias
      userInput.value = "";
      showConfirmModal.value = true;
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
const copyMessage = (content: string) => {
  if (!content) return;
  if (!navigator.clipboard) {
    const textArea = document.createElement("textarea");
    textArea.value = content;
    document.body.appendChild(textArea);
    textArea.select();
    try {
      document.execCommand("copy");
      showToast("已复制到剪贴板", "success");
    } catch (err) {
      console.error("Fallback: Oops, unable to copy", err);
      showToast("复制失败，请手动复制", "error");
    }
    document.body.removeChild(textArea);
    return;
  }
  navigator.clipboard
    .writeText(content)
    .then(() => {
      showToast("已复制到剪贴板", "success");
    })
    .catch((err) => {
      console.error("Failed to copy:", err);
      showToast("复制失败，请手动复制", "error");
    });
};

const exportData = async (traceId: string, format = 'xlsx') => {
  if (!traceId) return;
  try {
    const response = await axios.get(`/api/v1/chat/export/data/${traceId}`, {
      params: { format },
      responseType: 'blob'
    });

    const blob = new Blob([response.data], {
      type: format === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/csv'
    });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    link.setAttribute('download', `nanzi_export_${dateStr}_${traceId.slice(0, 8)}.${format}`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    showToast('数据导出成功', 'success');
  } catch (e) {
    console.error("Export failed", e);
    showToast('导出失败：未找到可导出数据', 'error');
  }
};
const regenerate = () => {
  if (isProcessing.value) return;
  // Find last user message
  const lastUserMsg = [...messages.value]
    .reverse()
    .find((m) => m.role === "user");
  if (lastUserMsg) {
    // Remove the last agent message if it was the response to this user message
    const lastMsg = messages.value[messages.value.length - 1];
    if (lastMsg && lastMsg.role === "agent") {
      messages.value.pop();
    }
    userInput.value = splitUserMessageContent(lastUserMsg.content).userPart;
    if (lastUserMsg.files?.length && chatInputRef.value) {
      chatInputRef.value.uploadedFiles = [...lastUserMsg.files];
    }
    sendMessage();
  }
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
  showToast(payload.created === false ? "该结果的订阅已存在" : "查询订阅已创建，可在黄金报表中管理", "success");
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
  if (action.id !== "brief") return handleQuickQuestion(action.query);
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

const handleQuickQuestion = async (content: string, action: "send" | "fill" = "send") => {
  if (!content) return;
  if (action === "send" && isProcessing.value) return;
  if (action === "send" && await handleSystemCommand(content)) return;
  userInput.value = content;
  if (action === "send") {
    sendMessage();
  }
};

const pendingGroundingAction = ref<Record<string, unknown> | null>(null);

const handleGroundingAction = async (
  payload: GroundingBlockedPayload | undefined,
  action: GroundingBlockedAction,
) => {
  if (!payload || isProcessing.value) return;
  if (action.kind === "grounding_retry") {
    pendingGroundingAction.value = {
      ...(action.payload || {}),
      type: "retry",
    };
    userInput.value = payload.retry_query;
    await sendMessage();
    pendingGroundingAction.value = null;
    return;
  }
  if (action.kind === "grounding_method") {
    pendingGroundingAction.value = {
      ...(action.payload || {}),
      type: "method",
    };
    userInput.value = String(action.payload?.message || "");
    await sendMessage();
    pendingGroundingAction.value = null;
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
  openPortalDrawer,
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
  lockToDataQueryAgentForDatasetMenu,
  switchToAutoRouting: switchToAuto,
  onQuickQuestion: handleQuickQuestion,
  hasDataQueryAgent,
  keepOpenStorageKey: "embed_portal_keep_open",
  pinStorageKey: "embed_portal_pinned",
  onPortalLoadingChange: (loading) => {
    if (loading) startPortalLoadingTips();
    else stopPortalLoadingTips();
  },
});

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
  await rawOpenKnowledgePortal();
  const kbExpert = resolveKnowledgeExpertAgent();
  if (kbExpert) {
    config.overrideAgentId = "";
    switchToExpert(kbExpert.id);
  }
};

watch(showPortalDrawer, (val) => {
  if (val) {
    closeKnowledgePortal();
  } else {
    stopPortalLoadingTips();
  }
});

watch(showKnowledgePortal, (val) => {
  if (!val) {
    // 只有在未打开数据门户的情况下，关闭知识库中心才退回到自动路由
    if (!showPortalDrawer.value) {
      config.routingMode = "auto";
      config.expertAgentId = "";
      saveRoutingSettings();
      showAutoRoutingHint.value = true;
      setTimeout(() => {
        showAutoRoutingHint.value = false;
      }, 3000);
    }
  }
});

// 监听上传文件的变更，保持知识库激活状态在抽屉卡片里是最新同步的
watch(
  () => chatInputRef.value?.uploadedFiles,
  () => {
    syncActiveDatasetsFromInput(chatInputRef.value);
  },
  { deep: true }
);



const pinnedDrawerDockOffsetRem = (exclude?: "portal" | "workspace" | "memory" | "knowledge") => {
  let rem = 0;
  if (exclude !== "portal" && showPortalDrawer.value && portalPinned.value) rem += 28;
  if (exclude !== "knowledge" && showKnowledgePortal.value && knowledgePinned.value) rem += 28;
  if (exclude !== "workspace" && showWorkspaceDrawer.value && workspacePinned.value) rem += 28;
  if (exclude !== "memory" && showMemoryDrawer.value && memoryPinned.value) rem += 28;
  return rem;
};

// 响应式抽屉宽度 refs（由各抽屉组件通过 v-model:drawerWidth / v-model:canvasWidth 实时同步）
const portalDrawerWidthReactive = ref(448);
const knowledgeDrawerWidthReactive = ref(448);
const workspaceDrawerWidthReactive = ref(448);
const canvasPinnedWidthReactive = ref(520);

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

const totalPinnedDrawerPx = computed(() => {
  if (isMobile.value) return 0;
  let px = 0;
  px += portalDrawerWidthPx.value;
  px += knowledgeDrawerWidthPx.value;
  px += workspaceDrawerWidthPx.value;
  px += canvasPinnedWidthPx.value;
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
    const execution_time_ms =
      data.execution_time_ms ??
      (nextStatus !== "pending"
        ? resolveStreamLogDurationMs(currentLog, data.execution_time_ms)
        : currentLog.execution_time_ms);
    msg.logs[existingIdx] = {
      ...currentLog,
      title: data.title || currentLog.title,
      details: data.details ?? currentLog.details,
      status: nextStatus,
      category: category !== "default" ? category : currentLog.category,
      execution_time_ms: execution_time_ms ?? currentLog.execution_time_ms,
      elapsed_time_ms: data.elapsed_time_ms ?? currentLog.elapsed_time_ms,
      started_at: currentLog.started_at ?? (data.status === "pending" ? Date.now() : data.started_at),
      rowFilterApplied: data.row_filter_applied === true || currentLog.rowFilterApplied,
    };
    return;
  }
  msg.logs.push({
    id: logId,
    title: data.title || "Log Info",
    details: data.details || "",
    status: (data.status as any) || "success",
    isExpanded: false,
    category,
    execution_time_ms: data.execution_time_ms ?? null,
    elapsed_time_ms: data.elapsed_time_ms ?? null,
    started_at: data.status === "pending" ? Date.now() : (data.started_at ?? null),
    rowFilterApplied: data.row_filter_applied === true,
  });
};

const applyPermissionStreamEvent = (msg: Message, data: any) => {
  applyStreamTraceId(msg, data);

  if (applyChatBIInsightEvent(msg, data) || applyChatBIMetadataGuideEvent(msg, data) || applyAgentHandoffEvent(msg, data)) return;

  if (dispatchAgentscopeStreamEvent(msg, data, addEmbedLogFromStream)) {
    if (data.type === "error") {
      if (msg.pendingPermission) msg.pendingPermission.status = "error";
      if (msg.pendingExternalExecution) msg.pendingExternalExecution.status = "error";
      msg.isThinking = false;
      msg.content += "\n\n> 服务异常: " + (data.content || "未知错误");
    } else if (data.content) {
      const piece = sanitizeStreamContent(String(data.content || ""));
      if (piece) {
        if (msg.isThoughtExpanded && !msg.content) msg.isThoughtExpanded = false;
        msg.content += piece;
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
    const thoughtText = data.thought || "No reasoning provided.";
    const agentName = data.selected_agent || "Unknown";
    const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
    addEmbedLogFromStream(msg, {
      id: "router_" + Date.now(),
      title: "智能路由决策",
      details: `思考过程:\n${thoughtText}\n\n最终选择: ${agentName} ${conf}`,
      status: "success",
      isRouter: true,
      category: "router",
      execution_time_ms: data.execution_time_ms ?? null,
    });
  } else if (data.type === "meta") {
    if (data.agent_name) msg.agentName = data.agent_name;
    if (data.agent_display_name) msg.agentDisplayName = data.agent_display_name;
    if (data.turn_type) msg.turnType = data.turn_type;
    if (data.prompt_tokens !== undefined) msg.prompt_tokens = data.prompt_tokens;
    if (data.completion_tokens !== undefined) msg.completion_tokens = data.completion_tokens;
    if (data.has_data_output) msg.hasDataOutput = true;
    if (data.permission_notice) msg.permissionNotice = data.permission_notice;
  } else if (data.type === "error") {
    if (msg.pendingPermission) msg.pendingPermission.status = "error";
    msg.isThinking = false;
    msg.content += "\n\n> 服务异常: " + (data.content || "未知错误");
  } else if (data.content) {
    const piece = sanitizeStreamContent(String(data.content || ""));
    if (piece) {
      if (msg.isThoughtExpanded && !msg.content) msg.isThoughtExpanded = false;
      msg.content += piece;
      if (msg.isThinking) msg.isThinking = false;
      resetStallTimer();
    }
  }
};

const submitPendingExternalExecution = async (msg: Message) => {
  const pending = msg.pendingExternalExecution;
  if (!pending || pending.status !== "pending") return;
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

const confirmPendingPermission = async (msg: Message, confirmed: boolean) => {
  const pending = msg.pendingPermission;
  if (!pending || pending.status !== "pending") return;
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
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const dataLines = parser.feed(decoder.decode(value, { stream: true }));
      for (const dataStr of dataLines) {
        if (dataStr === "[DONE]") continue;
        applyPermissionStreamEvent(msg, JSON.parse(dataStr));
      }
      scrollToBottom();
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
      const chartRegex = /(<chart>([\s\S]*?)<\/chart>)|(```(?:chart|echarts|json)\s*([\s\S]*?)```)/gi;
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

const sendMessage = async () => {
  const content = userInput.value.trim();
  const files = chatInputRef.value?.uploadedFiles ? Array.from(chatInputRef.value.uploadedFiles) as ChatFile[] : [];
  if ((!content && files.length === 0) || isProcessing.value) return;

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
    thinkingText: "任务处理中，请稍候...",
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
  abortController = new AbortController();
  try {
    const knowledgeDatasetIds = collectKnowledgeDatasetIds();
    const body: Record<string, unknown> = {
      messages: buildOutboundMessages(),
      stream: true,
      agent_id: (config.routingMode === "expert" && config.expertAgentId) ? config.expertAgentId : (config.overrideAgentId || config.agentId),
      enable_multi_agent: config.enableMultiAgent,
      conversation_id: conversationId.value,
      debug_options: {
        injected_context: injectedContext.value,
        model: config.overrideModel || undefined,
        enable_sql_plan: config.enableSqlPlan,
        grounding_enabled: config.enableGrounding,
        ignore_ltm: ltmIgnoredVal,
        hallucination_check: hallucinationCheckEnabled.value || undefined,
        knowledge_ragflow_similarity_threshold: knowledgeSimilarityThreshold.value,
        knowledge_ragflow_vector_weight: knowledgeVectorWeight.value,
        knowledge_ragflow_metadata_top_k: knowledgeMetadataTopK.value,
      },
      permission_options: {
        approval_mode: config.approvalMode || "ask",
      },
    };
    if (knowledgeDatasetIds.length > 0) {
      body.knowledge_dataset_ids = knowledgeDatasetIds;
    }
    if (pendingGroundingAction.value) {
      body.grounding_action = pendingGroundingAction.value;
      pendingGroundingAction.value = null;
    }
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

    let buffer = ""; // 缓冲区，用于处理跨 chunk 的不完整行
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // 最后一项可能是不完整的行，保留在缓冲区中
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmedLine = line.trim();
        if (!trimmedLine || !trimmedLine.startsWith("data: ")) continue;

        const dataStr = trimmedLine.slice(6).trim();
        if (dataStr === "[DONE]") continue;

        try {
          const data = JSON.parse(dataStr);
          applyStreamTraceId(agentMsg.value, data);
          if (data.type === "log") {
            if (agentMsg.value.isThinking && data.title) {
              agentMsg.value.thinkingText = `正在${data.title}...`;
            }
            addEmbedLogFromStream(agentMsg.value, data);
          } else if (mergeStreamCitations(agentMsg.value, data)) {
            // Citations are merged and de-duplicated by the shared stream normalizer.
          } else if (data.type === "router_log") {
            if (agentMsg.value.logs) {
              const thoughtText = data.thought || "No reasoning provided.";
              const agentName = data.selected_agent || "Unknown";
              const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
              agentMsg.value.logs.push({
                id: "router_" + Date.now(),
                title: "智能路由决策",
                details: `思考过程:\n${thoughtText}\n\n最终选择: ${agentName} ${conf}`,
                status: "success",
                isExpanded: false,
                isRouter: true,
                category: 'router',
                execution_time_ms: data.execution_time_ms ?? null,
              });
            }
          } else if (applyChatBIInsightEvent(agentMsg.value, data) || applyChatBIMetadataGuideEvent(agentMsg.value, data) || applyAgentHandoffEvent(agentMsg.value, data)) {
            // Additive ChatBI evidence event; answer content stays unchanged.
          } else if (dispatchAgentscopeStreamEvent(agentMsg.value, data, addEmbedLogFromStream)) {
            if (data.type === "permission_required" && thoughtTimer) {
              clearInterval(thoughtTimer);
              thoughtTimer = null;
            }
          } else if (data.type === "meta") {
            if (data.agent_name) {
              agentMsg.value.agentName = data.agent_name;
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
          } else if (data.type === "retraction") {
            agentMsg.value.content = data.content;
            if (data.final !== false) {
              agentMsg.value.isThinking = false;
            }
          } else if (data.type === "error") {
            agentMsg.value.isThinking = false;
            (agentMsg.value as any).status = "error";
            agentMsg.value.content += "\n\n> ❌ **服务异常**: " + (data.content || "未知错误");
          } else if (data.type === "answer" || data.content) {
            const piece = sanitizeStreamContent(String(data.content || ""));
            if (piece) {
              if (agentMsg.value.isThoughtExpanded && !agentMsg.value.content) {
                agentMsg.value.isThoughtExpanded = false;
              }
              agentMsg.value.content += piece;
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
            if (agentMsg.value.content) {
              agentMsg.value.isThinking = false;
            }
          } else if (data.status === "error") {
            agentMsg.value.isThinking = false;
            const errText = String(data.content || data.message || "未知错误").trim();
            agentMsg.value.content += `\n\n> ❌ **服务异常**: ${errText}`;
          }
          scrollToBottom();
        } catch (e) {
          console.error("Failed to parse SSE line:", trimmedLine, e);
        }
      }
    }
  } catch (e: any) {
    if (e.name === "AbortError") {
      agentMsg.value.content += "\n[用户终止]";
    } else {
      agentMsg.value.content += `\n[错误: ${e.message}]`;
    }
  } finally {
    isProcessing.value = false;
    agentMsg.value.isThinking = false;
    void refreshQuota();
    clearStallTimer();
    clearStalePendingTimer();
    showStalledPrompt.value = false;
    if (thoughtTimer) clearInterval(thoughtTimer);
    // Final cleanup: stop any remaining log spinners
    finalizeAllPendingStreamLogs(agentMsg.value);
    scrollToBottom();
    nextTick(() => {
      if (!isMobile.value) {
        chatInputRef.value?.focus();
      }
    });
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
  // Initialize or Retrieve Conversation ID
  const savedId = localStorage.getItem("yovole_embed_conv_id");
  if (savedId) {
    conversationId.value = savedId;
  } else {
    generateNewConversation();
  }
  // Load Routing Settings
  const savedMode = localStorage.getItem("yovole_routing_mode");
  if (savedMode) config.routingMode = savedMode;
  const savedExpert = localStorage.getItem("yovole_expert_agent_id");
  if (savedExpert) config.expertAgentId = savedExpert;
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
  const query = new URLSearchParams(window.location.search);
  if (query.get("token")) {
    const token = query.get("token")!;
    // 仅设置内存中的 config.token 参与校验；校验通过后再 syncValidatedCredentials，避免脏 URL 覆盖 localStorage
    config.token = token;
    console.log("[LifeCycle] Token found in URL (persist to storage only after validation).");
  }
  if (query.get("agent_id")) {
    const agentId = query.get("agent_id")!;
    config.agentId = agentId;
    switchToExpert(agentId);
  }
  if (query.get("theme")) applyTheme(query.get("theme")!);
  postMessageToHost({ type: "NANZI_WIDGET_READY" });
  if (config.token) {
    console.log("[LifeCycle] Initializing chat from existing token...");
    initChat();
    fetchUserInfo(); // Add explicit user fetch
    fetchAllowedAgents();
    fetchSlashCommands();
  }

  // Attach cleanup handlers to component instance scope
  (onUnmountHandlers as any).value = { onMessage, onOnline, onOffline };
});
onUnmounted(() => {
  window.removeEventListener("resize", updateWidth);
  window.removeEventListener("fullscreenchange", updateFullScreenStatus);
  const handlers = (onUnmountHandlers as any).value;
  if (handlers?.onMessage) window.removeEventListener("message", handlers.onMessage);
  if (handlers?.onOnline) window.removeEventListener("online", handlers.onOnline);
  if (handlers?.onOffline) window.removeEventListener("offline", handlers.onOffline);
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
}
:deep(.markdown-body pre) {
  margin-top: 1em;
  margin-bottom: 1em;
  padding: 1.25em 1em 1em 1em;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: auto;
  position: relative;
  box-shadow: none;
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
:deep(.markdown-body table) {
  display: block;
  width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin-bottom: 1em;
  font-size: 13px;
  -webkit-overflow-scrolling: touch;
}
:deep(.markdown-body pre) {
  max-width: 100%;
  overflow-x: auto;
  white-space: pre !important;
  -webkit-overflow-scrolling: touch;
}
/* Scrollbar styles for mobile tables/code */
:deep(.markdown-body pre)::-webkit-scrollbar,
:deep(.markdown-body table)::-webkit-scrollbar {
  height: 4px;
}
:deep(.markdown-body pre)::-webkit-scrollbar-thumb,
:deep(.markdown-body table)::-webkit-scrollbar-thumb {
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
:deep(.markdown-body th, .markdown-body td) {
  border: 1px solid #e5e7eb;
  padding: 8px 12px;
}
:deep(.markdown-body tr:nth-child(even)) {
  background-color: #f9fafb;
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
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3'/%3E%3C/svg%3E");
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
