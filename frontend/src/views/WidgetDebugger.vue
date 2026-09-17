<template>
  <div class="h-full flex flex-col space-y-4">
    <div class="flex justify-between items-center gap-4">
      <div class="flex items-center gap-3 min-w-0">
        <h1 class="text-2xl font-bold text-gray-800 truncate">组件调试台</h1>
        <button
          @click="openIntegrationGuide"
          class="h-8 w-8 inline-flex items-center justify-center rounded-full border border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100 hover:border-blue-300 transition-colors flex-shrink-0"
          title="查看 EmbedChat 集成指南"
          aria-label="查看 EmbedChat 集成指南"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M12 21a9 9 0 100-18 9 9 0 000 18z" />
          </svg>
        </button>
      </div>
      <div class="space-x-2 flex-shrink-0">
        <button @click="disconnect" class="px-3 py-1 bg-red-100 text-red-600 rounded hover:bg-red-200 text-sm">断开连接</button>
        <button @click="connect" class="px-3 py-1 bg-green-100 text-green-600 rounded hover:bg-green-200 text-sm">重新连接</button>
      </div>
    </div>

    <div class="flex flex-1 gap-6 overflow-hidden">
        <!-- Controls Panel -->
        <div class="w-1/3 bg-white rounded-xl shadow p-4 overflow-y-auto space-y-6">
            <!-- 1. Initialization -->
            <section class="space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-gray-500 uppercase tracking-wider">1. 初始化配置</h3>
                    <div class="inline-flex rounded-lg bg-gray-100 p-0.5 text-xs font-medium text-gray-600">
                        <button
                            type="button"
                            @click="config.authMode = 'ticket'"
                            :class="config.authMode === 'ticket' ? 'bg-white text-blue-600 shadow-sm font-semibold' : 'text-gray-500 hover:text-gray-700'"
                            class="px-2 py-1 rounded-md transition-all"
                        >
                            ⭐ Ticket 模式
                        </button>
                        <button
                            type="button"
                            @click="config.authMode = 'token'"
                            :class="config.authMode === 'token' ? 'bg-white text-blue-600 shadow-sm font-semibold' : 'text-gray-500 hover:text-gray-700'"
                            class="px-2 py-1 rounded-md transition-all"
                        >
                            API Token 模式
                        </button>
                    </div>
                </div>

                <!-- Ticket 模式表单 -->
                <div v-if="config.authMode === 'ticket'" class="p-3 bg-blue-50/60 border border-blue-100 rounded-lg space-y-2 text-xs">
                    <div class="flex items-center justify-between text-blue-800 font-medium">
                        <span>凭据模式：临时 Ticket（推荐）</span>
                        <span class="text-[11px] text-blue-600 bg-blue-100/80 px-1.5 py-0.5 rounded">自动签发</span>
                    </div>
                    <p class="text-gray-600 text-[11px] leading-relaxed">
                        点击发送时将自动调用 <code class="bg-white px-1 py-0.5 rounded border border-blue-200">/api/v1/embed/tickets</code> 申请一次性短时 Ticket 并发给 IFrame。
                    </p>
                    <div>
                        <label class="block text-[11px] text-gray-500 mb-1">指定用户名 (可选，默认当前用户)</label>
                        <input type="text" v-model="config.targetUsername" class="w-full text-xs border-gray-200 rounded-md bg-white" placeholder="留空代表当前登录账号">
                        <p class="text-[10px] text-amber-700/90 mt-1">
                            💡 普通用户仅可代表自己签发（留空或填写自己账号）；代他人签发需管理员或「代他人签发嵌入凭证」权限。
                        </p>
                    </div>
                </div>

                <!-- Token 模式表单 -->
                <div v-else class="space-y-2">
                    <div class="p-2.5 bg-rose-50 border border-rose-200 rounded-lg text-xs flex items-start gap-2 text-rose-700">
                        <svg class="w-4 h-4 text-rose-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                        </svg>
                        <div class="leading-relaxed">
                            <span class="font-semibold text-rose-800">兼容模式（不推荐在生产使用）：</span>
                            直接向前端传递长期 API Key 存在泄露风险，仅供存量旧系统兼容调试。新集成强烈建议切换为 <strong>⭐ Ticket 模式</strong>。
                        </div>
                    </div>
                    <div class="grid grid-cols-1 gap-2">
                        <label class="block text-xs text-gray-600 font-medium">API Token (长期 Key)</label>
                        <input type="text" v-model="config.token" class="w-full text-sm border-gray-300 rounded-md" placeholder="eyJ... 或 sk-...">
                    </div>
                </div>

                <div class="grid grid-cols-1 gap-2">
                    <label class="block text-xs">Agent ID</label>
                    <input type="text" v-model="config.agentId" class="w-full text-sm border-gray-300 rounded-md" placeholder="(空则自动路由)">
                </div>
                 <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs">Theme</label>
                        <select v-model="config.theme" class="w-full text-sm border-gray-300 rounded-md">
                            <option value="light">Light</option>
                            <option value="dark">Dark</option>
                        </select>
                    </div>
                     <div>
                        <label class="block text-xs">Primary Color</label>
                        <div class="flex items-center space-x-2 mt-1">
                             <input type="color" v-model="config.primaryColor" class="h-8 w-8 cursor-pointer rounded-md border border-gray-200">
                             <span class="text-xs text-gray-500">{{ config.primaryColor }}</span>
                        </div>
                    </div>
                </div>
                <button
                    @click="sendInit"
                    :disabled="isSubmittingInit"
                    class="w-full py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
                >
                    <span v-if="isSubmittingInit">正在签发 Ticket 并发送...</span>
                    <span v-else>发送 INIT_CONFIG {{ config.authMode === 'ticket' ? '(Ticket 换票)' : '' }}</span>
                </button>
            </section>

            <hr class="border-gray-100">

            <!-- 2. Context Injection -->
            <section class="space-y-3">
                <h3 class="text-sm font-bold text-gray-500 uppercase tracking-wider">2. 注入上下文</h3>
                <div class="grid grid-cols-1 gap-2">
                    <textarea v-model="contextPayload" rows="3" class="w-full text-xs font-mono border-gray-300 rounded-md" placeholder='{"business_context": {"ticket_id": "INC-1001", "current_url": "/home"}}'></textarea>
                </div>
                <button @click="sendContext" class="w-full py-2 bg-gray-600 text-white rounded-md text-sm hover:bg-gray-700">注入 UPDATE_CONTEXT</button>
            </section>

             <hr class="border-gray-100">

            <!-- 3. Commands & Reset -->
            <section class="space-y-3">
                <h3 class="text-sm font-bold text-gray-500 uppercase tracking-wider">3. 指令控制</h3>
                <div class="grid grid-cols-2 gap-2">
                    <button @click="resetSession" class="py-2 border border-red-200 text-red-600 rounded-md text-sm hover:bg-red-50">重置会话</button>
                    <button @click="toggleExpand" class="py-2 border border-gray-200 text-gray-600 rounded-md text-sm hover:bg-gray-50">
                        {{ isExpanded ? '切换小窗口' : '切换全屏' }}
                    </button>
                </div>
                <div class="flex gap-2">
                    <input v-model="commandInput" type="text" placeholder="/help" class="flex-1 text-sm border-gray-300 rounded-md">
                    <button @click="sendCommand" class="px-3 bg-purple-100 text-purple-700 rounded-md text-sm hover:bg-purple-200">发送指令</button>
                </div>
            </section>
            
            <!-- Logs -->
            <section class="bg-gray-900 text-green-400 p-3 rounded-md font-mono text-xs max-h-40 overflow-y-auto">
                <div v-for="(log, i) in logs" :key="i">{{ log }}</div>
                <div v-if="logs.length === 0" class="text-gray-600 italic">等待消息...</div>
            </section>
        </div>

        <!-- Preview Panel -->
        <div class="flex-1 bg-gray-200 rounded-xl flex items-center justify-center relative p-8">
            <div class="absolute inset-0 flex items-center justify-center pointer-events-none opacity-10">
                <span class="text-6xl font-black text-gray-400">HOST PAGE</span>
            </div>
            
            <!-- IFrame Container -->
            <div class="relative bg-white shadow-2xl transition-all duration-300 overflow-hidden" :style="{ width: frameWidth, height: frameHeight, borderRadius: '12px' }">
                <iframe 
                    v-if="iframeUrl"
                    ref="widgetFrame"
                    :src="iframeUrl"
                    width="100%"
                    height="100%"
                    frameborder="0"
                    class="w-full h-full"
                ></iframe>
                <div v-else class="flex items-center justify-center h-full text-gray-400 text-sm">
                    Widget Disconnected
                </div>
            </div>
	        </div>
	    </div>

        <div
          v-if="showIntegrationGuide"
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-sm p-4"
          @click.self="showIntegrationGuide = false"
        >
          <div class="w-full max-w-5xl max-h-[88vh] bg-white rounded-xl shadow-2xl border border-gray-200 overflow-hidden flex flex-col">
            <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between gap-4">
              <div class="min-w-0">
                <h2 class="text-lg font-black text-gray-900">EmbedChat 集成指南</h2>
                <p class="text-xs text-gray-500 mt-1">选择一种方式复制代码，示例会按当前登录态自动填入域名、API Key 和智能体模式。</p>
              </div>
              <button
                @click="showIntegrationGuide = false"
                class="h-9 w-9 inline-flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors flex-shrink-0"
                title="关闭"
                aria-label="关闭"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <!-- 顶部红色安全提示条 -->
            <div class="mx-5 mt-3 p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs flex items-start gap-2.5 text-rose-700">
              <svg class="w-4 h-4 text-rose-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <div class="leading-relaxed">
                <span class="font-bold text-rose-800">安全规范提示：</span>
                旧版直接在前端传递长期 API Key 的方式仅作为存量系统向后兼容，存在凭证外泄风险，<strong>不推荐在生产环境中使用</strong>。新系统集成强烈推荐使用 <strong>⭐ 临时 Ticket 模式</strong>（由宿主后端内网申请 5 分钟一次性票据，前端免密兑换并支持滑动续期）。
              </div>
            </div>

            <div class="border-b border-gray-100 px-4 overflow-x-auto shrink-0">
              <div class="flex items-center gap-1 min-w-max h-12">
                <button
                  v-for="tab in integrationTabs"
                  :key="tab.id"
                  @click="activeIntegrationTab = tab.id"
                  class="h-12 px-4 inline-flex items-center whitespace-nowrap text-sm leading-none font-bold border-b-2 transition-colors flex-shrink-0"
                  :class="activeIntegrationTab === tab.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-200'"
                >
                  {{ tab.label }}
                </button>
              </div>
            </div>

            <div class="flex-1 overflow-y-auto p-5 bg-gray-50">
              <!-- Markdown 完整文档专属视图 -->
              <div v-if="activeIntegrationTab === 'markdown_docs'" class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                <div class="px-6 py-4 border-b border-gray-100 bg-white flex items-center justify-between gap-4 sticky top-0 z-10">
                  <div>
                    <h3 class="text-base font-bold text-gray-900"> NanZi 智能体平台嵌入式组件集成指南 (EmbedChat)</h3>
                    <p class="text-xs text-gray-500 mt-0.5">官方完整技术白皮书：涵盖架构时序、Ticket 签发、多语言后端/前端范例、PostMessage 协议与 FAQ</p>
                  </div>
                  <div class="flex items-center gap-2 flex-shrink-0">
                    <button
                      @click="copyGuideCode"
                      class="px-3.5 py-1.5 rounded-lg text-xs font-bold bg-blue-600 text-white hover:bg-blue-700 transition-colors flex items-center gap-1.5 shadow-sm"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                      </svg>
                      复制 Markdown 全文
                    </button>
                  </div>
                </div>
                <div
                  class="p-6 text-sm text-gray-800 leading-relaxed font-sans prose prose-slate max-w-none select-text"
                  v-html="renderedMarkdownDoc"
                ></div>
              </div>

              <!-- 其他代码生成器 Tabs 视图 -->
              <template v-else>
                <div class="mb-5 bg-white border border-gray-200 rounded-lg p-4">
                  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="min-w-0">
                      <div class="text-xs font-black text-gray-400 uppercase tracking-wider mb-1">当前域名</div>
                      <div class="font-mono text-xs text-gray-700 truncate bg-gray-50 border border-gray-100 rounded-md px-2 py-2">{{ integrationHost }}</div>
                    </div>
                    <div class="min-w-0">
                      <div class="text-xs font-black text-gray-400 uppercase tracking-wider mb-1">API Key</div>
                      <div
                        class="font-mono text-xs truncate border rounded-md px-2 py-2"
                        :class="integrationHasRealApiKey ? 'text-emerald-700 bg-emerald-50 border-emerald-100' : 'text-amber-700 bg-amber-50 border-amber-100'"
                        :title="integrationHasRealApiKey ? integrationApiKey : '当前浏览器未读取到登录 API Key，将使用占位值'"
                      >
                        {{ integrationHasRealApiKey ? maskApiKey(integrationApiKey) : integrationApiKey }}
                      </div>
                    </div>
                    <div class="min-w-0 lg:col-span-2">
                      <div class="text-xs font-black text-gray-400 uppercase tracking-wider mb-1">智能体模式</div>
                      <div class="grid grid-cols-1 sm:grid-cols-[9rem_minmax(0,1fr)] gap-2">
                        <select v-model="integrationAgentMode" class="w-full min-w-0 text-xs border-gray-300 rounded-md h-9">
                          <option value="auto">自动路由</option>
                          <option value="agent">指定智能体</option>
                        </select>
                        <select
                          v-model="selectedIntegrationAgentId"
                          class="w-full min-w-0 max-w-full text-xs border-gray-300 rounded-md h-9 truncate"
                          :disabled="integrationAgentMode === 'auto' || integrationAgents.length === 0"
                        >
                          <option v-if="integrationAgents.length === 0" value="">暂无可用智能体</option>
                          <option v-for="agent in integrationAgents" :key="agent.id" :value="agent.id">
                            {{ agent.display_name || agent.name }} ({{ agent.id }})
                          </option>
                        </select>
                      </div>
                    </div>
                  </div>
                  <div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                    <span class="px-2 py-1 rounded bg-gray-50 border border-gray-100">当前生成：{{ integrationAgentMode === 'auto' ? '不传 agent_id，由平台自动路由' : `指定 ${selectedIntegrationAgentLabel}` }}</span>
                    <span v-if="!integrationHasRealApiKey" class="px-2 py-1 rounded bg-amber-50 border border-amber-100 text-amber-700">未读到本地 API Key，复制前请先登录或手动替换占位值</span>
                  </div>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)] gap-5">
                  <aside class="space-y-3">
                    <div class="bg-white border border-gray-200 rounded-lg p-4">
                      <div class="text-xs font-black text-gray-400 uppercase tracking-wider mb-2">适用场景</div>
                      <p class="text-sm text-gray-700 leading-relaxed">{{ activeIntegrationTabData.summary }}</p>
                    </div>
                    <div class="bg-blue-50 border border-blue-100 rounded-lg p-4">
                      <div class="text-xs font-black text-blue-500 uppercase tracking-wider mb-2">关键点</div>
                      <ul class="space-y-2 text-sm text-blue-900">
                        <li v-for="point in activeIntegrationTabData.points" :key="point" class="flex gap-2 leading-relaxed">
                          <span class="mt-2 h-1.5 w-1.5 rounded-full bg-blue-500 flex-shrink-0"></span>
                          <span>{{ point }}</span>
                        </li>
                      </ul>
                    </div>
                  </aside>

	                <section class="bg-white border border-gray-200 rounded-lg overflow-hidden min-w-0">
	                  <div class="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 bg-white">
                      <div class="min-w-0">
                        <h3 class="font-black text-gray-900 truncate">{{ activeIntegrationTabData.title }}</h3>
                        <p class="text-xs text-gray-500 mt-0.5">{{ activeIntegrationTabData.caption }}</p>
                      </div>
                      <button
                        @click="copyGuideCode"
                        class="px-3 py-1.5 rounded-md text-xs font-bold bg-gray-900 text-white hover:bg-black transition-colors flex-shrink-0"
                      >
	                      复制代码
	                    </button>
	                  </div>
                      <div class="p-4 border-b border-gray-100 bg-slate-50">
                        <div class="text-xs font-black text-gray-400 uppercase tracking-wider mb-3">接入效果预览</div>
                        <div class="relative h-56 rounded-lg border border-gray-200 bg-white overflow-hidden shadow-inner">
                          <div class="absolute inset-x-0 top-0 h-9 bg-slate-900 flex items-center px-3 gap-2">
                            <span class="h-2.5 w-2.5 rounded-full bg-red-400"></span>
                            <span class="h-2.5 w-2.5 rounded-full bg-yellow-400"></span>
                            <span class="h-2.5 w-2.5 rounded-full bg-green-400"></span>
                            <span class="ml-2 text-[10px] font-bold text-slate-300">Host Business Page</span>
                          </div>

                          <template v-if="activeIntegrationTab === 'iframe'">
                            <div class="absolute left-4 right-4 top-14 bottom-4 rounded-lg border border-blue-200 bg-blue-50 shadow-sm overflow-hidden">
                              <div class="h-9 bg-blue-600 text-white px-3 flex items-center justify-between text-xs font-bold">
                                <span>EmbedChat IFrame</span>
                                <span>100% x 640px</span>
                              </div>
                              <div class="p-4 space-y-3">
                                <div class="h-3 w-2/3 rounded bg-blue-200"></div>
                                <div class="h-16 rounded-lg bg-white border border-blue-100"></div>
                                <div class="h-9 rounded-full bg-blue-600/90"></div>
                              </div>
                            </div>
                          </template>

                          <template v-else-if="activeIntegrationTab === 'postmessage'">
                            <div class="absolute left-4 top-14 bottom-4 w-[52%] rounded-lg bg-slate-100 border border-slate-200 p-3">
                              <div class="h-3 w-20 rounded bg-slate-300 mb-3"></div>
                              <div class="space-y-2">
                                <div class="h-3 rounded bg-slate-200"></div>
                                <div class="h-3 w-4/5 rounded bg-slate-200"></div>
                                <div class="h-16 rounded border border-slate-200 bg-white"></div>
                              </div>
                            </div>
                            <div class="absolute right-4 top-14 bottom-4 w-[38%] rounded-lg border border-emerald-200 bg-white shadow-lg overflow-hidden">
                              <div class="h-8 bg-emerald-600 text-white px-3 flex items-center text-xs font-bold">Ready -> INIT_CONFIG</div>
                              <div class="p-3 space-y-2">
                                <div class="flex items-center gap-2 text-[10px] text-emerald-700 font-bold">
                                  <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
                                  Token via postMessage
                                </div>
                                <div class="h-14 rounded-lg bg-emerald-50 border border-emerald-100"></div>
                                <div class="h-7 rounded-full bg-emerald-600/90"></div>
                              </div>
                            </div>
                          </template>

                          <template v-else-if="activeIntegrationTab === 'floating'">
                            <div class="absolute left-4 top-14 right-4 bottom-4 rounded-lg bg-slate-100 border border-slate-200 p-4">
                              <div class="grid grid-cols-3 gap-3 h-full">
                                <div class="rounded bg-white border border-slate-200"></div>
                                <div class="rounded bg-white border border-slate-200"></div>
                                <div class="rounded bg-white border border-slate-200"></div>
                              </div>
                            </div>
                            <div class="absolute right-5 bottom-5 h-24 w-32 rounded-xl bg-white border border-blue-200 shadow-2xl overflow-hidden">
                              <div class="h-7 bg-blue-600 text-white px-2 flex items-center text-[10px] font-bold">AI Assistant</div>
                              <div class="absolute right-1.5 top-1.5 h-4 w-4 rounded-full bg-white/20 text-white flex items-center justify-center text-[10px] font-black">-</div>
                              <div class="p-2 space-y-1">
                                <div class="h-2 rounded bg-blue-100"></div>
                                <div class="h-2 w-2/3 rounded bg-blue-100"></div>
                                <div class="h-5 rounded-full bg-blue-600/90 mt-2"></div>
                              </div>
                            </div>
                            <div class="absolute right-5 bottom-5 translate-x-4 translate-y-4 h-11 w-11 rounded-full bg-blue-600 text-white shadow-xl flex items-center justify-center text-xs font-black ring-4 ring-white">AI</div>
                          </template>

                          <template v-else>
                            <div class="absolute left-4 top-14 bottom-4 w-[45%] rounded-lg border border-violet-200 bg-white shadow-sm overflow-hidden">
                              <div class="h-8 bg-violet-600 text-white px-3 flex items-center text-xs font-bold">ticket-sidebar-ai</div>
                              <div class="p-3 space-y-2">
                                <div class="h-3 rounded bg-violet-100"></div>
                                <div class="h-3 w-3/4 rounded bg-violet-100"></div>
                                <div class="h-14 rounded-lg border border-violet-100 bg-violet-50"></div>
                              </div>
                            </div>
                            <div class="absolute right-4 top-14 bottom-4 w-[45%] rounded-lg border border-amber-200 bg-white shadow-sm overflow-hidden">
                              <div class="h-8 bg-amber-500 text-white px-3 flex items-center text-xs font-bold">report-page-ai</div>
                              <div class="p-3 space-y-2">
                                <div class="h-3 rounded bg-amber-100"></div>
                                <div class="h-3 w-2/3 rounded bg-amber-100"></div>
                                <div class="h-14 rounded-lg border border-amber-100 bg-amber-50"></div>
                              </div>
                            </div>
                          </template>
                        </div>
                      </div>

	                  <div class="p-4 bg-slate-950 overflow-x-auto">
	                    <pre class="font-mono text-xs text-slate-100 leading-relaxed"><code>{{ activeIntegrationTabData.code }}</code></pre>
	                  </div>
	                </section>
                </div>
              </template>
            </div>
          </div>
        </div>
	  </div>
	</template>
	
	<script setup lang="ts">
	import { computed, ref, reactive, onMounted, onUnmounted } from 'vue';
    import axios from '../utils/axios';
    import { useToast } from '../composables/useToast';
    import { copyToClipboard } from '../utils/clipboard';
    import { renderSafeMarkdownPreview } from '../utils/safeMarkdown';

    interface IntegrationTab {
        id: string;
        label: string;
        title: string;
        caption: string;
        summary: string;
        points: string[];
        code: string;
    }

    interface IntegrationAgent {
        id: string;
        name: string;
        display_name?: string;
    }

	const widgetFrame = ref<HTMLIFrameElement | null>(null);
	const logs = ref<string[]>([]);
	const iframeUrl = ref('');
    const showIntegrationGuide = ref(false);
    const activeIntegrationTab = ref('markdown_docs');
    const { showToast } = useToast();

const config = reactive({
    authMode: 'ticket' as 'ticket' | 'token',
    targetUsername: '',
    token: '',
    agentId: '',
    theme: 'light',
    primaryColor: '#3b82f6',
});

const isSubmittingInit = ref(false);

const contextPayload = ref('{\n  "business_context": {\n    "ticket_id": "INC-1001",\n    "current_page": "ticket-detail"\n  }\n}');
	const commandInput = ref('/new');

    const integrationAgentMode = ref<'auto' | 'agent'>('auto');
    const integrationAgents = ref<IntegrationAgent[]>([]);
    const selectedIntegrationAgentId = ref('');

    const integrationHost = computed(() => {
        return typeof window !== 'undefined' ? window.location.origin : '';
    });
    const integrationApiKey = computed(() => config.token.trim() || 'CURRENT_USER_API_KEY');
    const integrationHasRealApiKey = computed(() => Boolean(config.token.trim()));
    const selectedIntegrationAgent = computed(() => {
        return integrationAgents.value.find((agent) => agent.id === selectedIntegrationAgentId.value);
    });
    const selectedIntegrationAgentLabel = computed(() => {
        const agent = selectedIntegrationAgent.value;
        if (!agent) return selectedIntegrationAgentId.value || '未选择智能体';
        return `${agent.display_name || agent.name} (${agent.id})`;
    });

    const maskApiKey = (key: string) => {
        if (key.length <= 12) return key;
        return `${key.slice(0, 6)}...${key.slice(-4)}`;
    };
    /**
     * 取用于第三方接入代码的凭据。
     *
     * 这里必须用长期有效的真实 API Key，不能用浏览器会话令牌——会话令牌会随用户登出
     * 而失效，写进给第三方的接入代码后会让集成随时中断。凭据按需向后端索取，
     * 前端不再从 localStorage 读取（那里只有会话令牌）。
     */
    const resolveStoredApiKey = async (): Promise<string> => {
        try {
            const raw = localStorage.getItem('user_info');
            const userId = raw ? JSON.parse(raw)?.user_id : null;
            if (!userId) return '';
            const res = await axios.get(`/api/portal/management/api-key/${userId}`);
            return res.data?.api_key || '';
        } catch (e) {
            console.warn('[WidgetDebugger] 获取 API Key 失败，接入代码将使用占位值', e);
            return '';
        }
    };

    const escapeJsString = (value: string) => value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const buildAgentQueryParam = () => {
        const agentId = buildAgentInitValue();
        return agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
    };
    const buildAgentInitValue = () => {
        if (integrationAgentMode.value !== 'agent') return '';
        return selectedIntegrationAgentId.value || integrationAgents.value[0]?.id || '';
    };
    const buildAgentInitLine = () => {
        const agentEntry = { agent_id: buildAgentInitValue() };
        return agentEntry.agent_id ? `\n      agent_id: '${escapeJsString(agentEntry.agent_id)}',` : '';
    };

    const integrationTabs = computed<IntegrationTab[]>(() => {
        const host = integrationHost.value;
        const token = integrationApiKey.value;
        const encodedToken = encodeURIComponent(token);
        const theme = config.theme || 'light';
        // 保留该参数生成器作为接入代码兼容入口；Ticket 模式通过 body 传 agent_id。
        const agentQueryParam = buildAgentQueryParam();
        void agentQueryParam;
        const agentInitLine = buildAgentInitLine();

        return [
        {
            id: 'markdown_docs',
            label: '📖 完整接入开发文档 (Markdown)',
            title: ' NanZi 智能体平台嵌入式组件集成指南 (EmbedChat)',
            caption: '官方完整技术白皮书：涵盖架构时序、Ticket 签发、多语言后端/前端范例、PostMessage 协议与 FAQ。',
            summary: '包含完整的 Markdown 技术规格说明书，可直接在线浏览或全文复制。',
            points: ['包含 Java/Python/Go/Node.js 服务端 Ticket 签发范例', '包含 Vue 3 / React / 原生 HTML 前端接入代码', '包含 PostMessage 指令与事件完整协议速查', '包含常见报错与排错 FAQ'],
            code: `# NanZi 智能体平台嵌入式组件集成指南 (EmbedChat Integration Guide)

本文档旨在指导第三方业务系统（如 OA、CRM、ERP、门户系统等）如何安全、高效、深度地集成 NanZi AI Agent 对话组件（EmbedChat）。

---

## 一、集成架构与认证原理 (Embed Ticket 模式)

在企业级生产环境中，**强烈推荐使用 Embed Ticket 临时票据体系**。该架构实现了**长期主 API Key 零泄露**与**用户代客身份（Impersonation）安全绑定**。

### 1. 三步标准交互时序
1. **宿主后端 (Server-to-Server)**：在企业内网调用 \`POST ${host}/api/v1/embed/tickets\` 为当前登录员工申请 5 分钟一次性 Ticket（长期主 Key 留在宿主后端环境变量，绝不出内网）；
2. **前端 IFrame 接入与自动兑换**：前端加载 \`<iframe src="${host}/embed/chat?ticket=emt_xxx">\`，组件内部自动核销 Ticket 并换取 24 小时短期 Session Token；
3. **活跃滑动自动续期 (Sliding TTL)**：只要用户在持续聊天发消息，服务端自动将有效时间重新顺延 24 小时；闲置超 24 小时后自动释放。若超时断开，前端监听到 \`INIT_FAILURE\` 事件后，重新申请 Ticket 发送 \`RESET_SESSION\` 即可达成无感自动重连。

---

## 二、服务端 Ticket 签发接口规范

- **请求方式**：\`POST ${host}/api/v1/embed/tickets\`
- **请求头**：
  \`\`\`http
  Content-Type: application/json
  X-API-Key: ${encodedToken}
  \`\`\`
- **请求参数 (JSON Body)**：
  \`\`\`json
  {
    "username": "zhangsan",                   // 必填：目标业务员工用户名（代表谁提问）
    "agent_id": "${buildAgentInitValue() || 'sys-agent-chatbi'}", // 可选：指定智能体 ID
    "expires_in": 300                         // 可选：Ticket 有效期（秒，默认 300，一次性核销）
  }
  \`\`\`
- **响应示例 (JSON)**：
  \`\`\`json
  {
    "code": 200,
    "message": "success",
    "data": {
      "ticket": "emt_a8f9c2d1e0b3456789abcdef",
      "expires_in": 300,
      "target_user": {
        "user_id": 102,
        "user_name": "zhangsan",
        "real_name": "张三"
      }
    }
  }
  \`\`\`

---

## 三、多语言后端签发 Ticket 示例

### 1. Java (Spring Boot)
\`\`\`java
@GetMapping("/embed-ticket")
public ResponseEntity<?> getEmbedTicket(@RequestAttribute("user") String username) {
    HttpHeaders headers = new HttpHeaders();
    headers.setContentType(MediaType.APPLICATION_JSON);
    headers.set("X-API-Key", "${escapeJsString(token)}");

    Map<String, Object> body = new HashMap<>();
    body.put("username", username);
    body.put("agent_id", "${escapeJsString(buildAgentInitValue() || 'sys-agent-chatbi')}");
    body.put("expires_in", 300);

    HttpEntity<Map<String, Object>> request = new HttpEntity<>(body, headers);
    ResponseEntity<Map> resp = restTemplate.postForEntity("${host}/api/v1/embed/tickets", request, Map.class);
    return ResponseEntity.ok(resp.getBody().get("data"));
}
\`\`\`

### 2. Python (FastAPI / Requests)
\`\`\`python
@router.get("/embed-ticket")
async def get_ai_embed_ticket(username: str = "zhangsan"):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "${host}/api/v1/embed/tickets",
            headers={"X-API-Key": "${escapeJsString(token)}"},
            json={"username": username, "agent_id": "${escapeJsString(buildAgentInitValue() || 'sys-agent-chatbi')}", "expires_in": 300}
        )
        return resp.json()["data"]
\`\`\`

---

## 四、前端双向通信协议 (PostMessage)

### 1. 下行指令 (Host -> Widget)
- \`INIT_CONFIG\`: \`{ ticket: 'emt_xxx', theme: 'light', business_context: { ... } }\`
- \`RESET_SESSION\`: \`{ ticket: 'emt_new_xxx' }\` (超时重连续签)
- \`UPDATE_CONTEXT\`: \`{ payload: { ... } }\` (动态同步业务对象)

### 2. 上行事件 (Widget -> Host)
- \`NANZI_WIDGET_READY\`: 组件就绪
- \`INIT_SUCCESS\`: 鉴权成功并就绪 (携带 user 信息)
- \`INIT_FAILURE\`: \`{ reason: 'invalid_ticket' }\` (会话过期，需重新申请 ticket 续期)
- \`USER_FEEDBACK\`: \`{ feedback: 'up' | 'down' }\` (用户点赞点踩)`,
        },
        {
            id: 'ticket',
            label: '⭐ 快速 Ticket IFrame (生产推荐)',
            title: '服务端生成 Ticket，前端 URL 嵌入',
            caption: '企业级生产推荐：长期 API Key 留在宿主后端，前端仅使用一次性短时 Ticket，自动滑动续期。',
            summary: '宿主后端代表用户调用 /api/v1/embed/tickets 获得 ticket，前端仅传 ticket 即可完成安全鉴权。',
            points: ['长期 API Key 永不暴露给浏览器', 'Ticket 5分钟有效且一次性核销（阅后即焚）', '会话活跃自动滑动延长 24 小时 TTL', '支持超时自动静默重连'],
            code: `<!-- 1. 宿主后端 (Node.js/Java/Python/Go)：代表登录员工向南孜申请一次性 Ticket -->
// POST ${host}/api/v1/embed/tickets
// Headers: { "X-API-Key": "YOUR_SERVER_SYSTEM_KEY" }
// Body: { "username": "zhangsan"${agentInitLine ? ',' + agentInitLine.trim() : ''} }
// Response: { "data": { "ticket": "emt_xxx..." } }

<!-- 2. 宿主前端 HTML：直接传入 ticket 渲染 IFrame -->
<iframe
  src="${host}/embed/chat?ticket=YOUR_SERVER_OBTAINED_TICKET&theme=${encodeURIComponent(theme)}"
  width="100%"
  height="640"
  frameborder="0"
  style="border:0;border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,.12);"
></iframe>`,
        },
        {
            id: 'postmessage',
            label: '⭐ PostMessage 交互 (推荐)',
            title: 'PostMessage 结合 Ticket 初始化与双向通信',
            caption: '推荐生产使用：iframe src 干净无参，宿主通过 postMessage 传递 Ticket 与业务上下文，并支持超时自动重连。',
            summary: '宿主页面监听 NANZI_WIDGET_READY 后传入 Ticket；监听 INIT_FAILURE 实现会话超时静默自动续期。',
            points: ['URL 不带任何敏感参数', '通过 Ticket 安全鉴权', '监听 INIT_FAILURE 实现静默续期', '支持注入 business_context 业务上下文'],
            code: `<iframe
  id="nanzi-agent-frame"
  src="${host}/embed/chat?instance_id=ops-assistant"
  width="100%"
  height="640"
  frameborder="0"
  style="border:0;border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,.12);"
></iframe>

<script>
const frame = document.getElementById('nanzi-agent-frame');
const targetOrigin = '${host}';

// 1. 从宿主后端获取一次性 Ticket
async function getTicketFromHost() {
  const res = await fetch('/api/my-host/get-embed-ticket');
  const data = await res.json();
  return data.ticket;
}

window.addEventListener('message', async (event) => {
  if (event.origin !== targetOrigin) return;
  const data = event.data || {};
  if (data.source !== 'nanzi-agent-embed') return;

  // 2. 组件就绪时，发送 Ticket 进行免密安全鉴权
  if (data.type === 'NANZI_WIDGET_READY') {
    const ticket = await getTicketFromHost();
    frame.contentWindow.postMessage({
      type: 'INIT_CONFIG',
      instance_id: 'ops-assistant',
      ticket: ticket,${agentInitLine}
      theme: '${escapeJsString(theme)}',
      business_context: {
        current_page: '容量看板',
        business_object: 'capacity-overview',
        operator_label: '张三'
      },
      styleVars: {
        '--primary-color': '#1677ff'
      }
    }, targetOrigin);
  }

  // 3. 会话空闲超时后，静默申请新 Ticket 进行重连
  if (data.type === 'INIT_FAILURE' && (data.reason === 'invalid_ticket' || data.reason === 'invalid_token')) {
    console.warn('会话已超时，正在静默获取新 Ticket 续期...');
    const newTicket = await getTicketFromHost();
    frame.contentWindow.postMessage({
      type: 'RESET_SESSION',
      ticket: newTicket
    }, targetOrigin);
  }
});
<\/script>`,
        },
        {
            id: 'floating',
            label: '⭐ 悬浮助手模式',
            title: '右下角悬浮展开助手 (Ticket 模式)',
            caption: '适合接入已有业务系统，默认收起为右下角悬浮球，点击后换票展开对话。',
            summary: '点击悬浮入口时从后端获取一次性 Ticket 并渲染对话窗口，兼顾整洁与安全。',
            points: ['不占用业务主布局', '点击展开时按需换票', '支持一键收起恢复气泡', 'URL 干净无长期 Key'],
            code: `<div id="nanzi-widget-shell" class="nanzi-widget-shell collapsed">
  <button id="nanzi-widget-toggle" class="nanzi-widget-toggle">💬 AI 助手</button>
  <button id="nanzi-widget-collapse" class="nanzi-widget-collapse" title="收起助手" aria-label="收起助手">−</button>
  <iframe
    id="nanzi-agent-frame"
    src="about:blank"
    frameborder="0"
  ></iframe>
</div>

<style>
.nanzi-widget-shell {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 9999;
  width: 420px;
  height: 680px;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 18px 48px rgba(15,23,42,.24);
  background: #fff;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.nanzi-widget-shell.collapsed {
  width: 56px;
  height: 56px;
  border-radius: 999px;
}
.nanzi-widget-shell iframe { width: 100%; height: 100%; border: 0; }
.nanzi-widget-shell.collapsed iframe { display: none; }
.nanzi-widget-collapse {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 999px;
  background: rgba(15, 23, 42, .72);
  color: #fff;
  font-size: 18px;
  line-height: 28px;
  cursor: pointer;
}
.nanzi-widget-shell.collapsed .nanzi-widget-collapse { display: none; }
.nanzi-widget-toggle {
  display: none;
  width: 100%;
  height: 100%;
  border: 0;
  border-radius: 999px;
  background: #1677ff;
  color: #fff;
  font-weight: 800;
}
.nanzi-widget-shell.collapsed .nanzi-widget-toggle {
  display: block;
}
</style>

<script>
const shell = document.getElementById('nanzi-widget-shell');
const frame = document.getElementById('nanzi-agent-frame');
const targetOrigin = '${host}';

document.getElementById('nanzi-widget-toggle').onclick = () => {
  shell.classList.remove('collapsed');
};
document.getElementById('nanzi-widget-collapse').onclick = () => {
  shell.classList.add('collapsed');
};

window.addEventListener('message', (event) => {
  if (event.origin !== targetOrigin) return;
  const data = event.data || {};
  if (data.source !== 'nanzi-agent-embed') return;

  if (data.type === 'NANZI_WIDGET_READY') {
    frame.contentWindow.postMessage({
      type: 'INIT_CONFIG',
      instance_id: 'floating-ai',
      token: '${escapeJsString(token)}',${agentInitLine}
      theme: '${escapeJsString(theme)}'
    }, targetOrigin);
  }
});
<\/script>`,
        },
        {
            id: 'context',
            label: '多实例/上下文',
            title: '同步业务上下文和多实例隔离',
            caption: '适合页面上有多个助手，或需要让 Agent 感知当前业务对象的场景。',
            summary: '为每个组件设置 instance_id，并在页面状态变化时发送 UPDATE_CONTEXT 或 SEND_COMMAND。',
            points: ['多 iframe 不串消息', 'Agent 可读取当前页面、工单、资产等上下文', integrationAgentMode.value === 'auto' ? '适合让平台按上下文自动调度专家' : '适合让指定专家读取当前业务对象'],
            code: `const frame = document.getElementById('nanzi-agent-frame');
const targetOrigin = '${host}';
const instanceId = 'ticket-sidebar-ai';

function postToAgent(message) {
  frame.contentWindow.postMessage({
    instance_id: instanceId,
    ...message
  }, targetOrigin);
}

function syncTicketContext(ticket) {
  postToAgent({
    type: 'UPDATE_CONTEXT',
    payload: {
      business_context: {
        current_page: 'ticket-detail',
        ticket_id: ticket.id,
        title: ticket.title,
        priority: ticket.priority,
        owner_dept: ticket.ownerDept
      }
    }
  });
}

function askAgentToSummarize() {
  postToAgent({
    type: 'SEND_COMMAND',
    command: '请总结当前工单，并给出下一步处理建议'
  });
}

function resetAgentSession(newToken) {
  postToAgent({
    type: 'RESET_SESSION',
    new_token: newToken
  });
}`,
        },
        ];
    });

    const activeIntegrationTabData = computed<IntegrationTab>(() => {
        return integrationTabs.value.find((tab) => tab.id === activeIntegrationTab.value) || integrationTabs.value[0]!;
    });

    const renderedMarkdownDoc = computed(() => {
        const mdTab = integrationTabs.value.find((tab) => tab.id === 'markdown_docs');
        return renderSafeMarkdownPreview(mdTab?.code || '');
    });

// Simulate Host Size control
const frameWidth = ref('100%');
const frameHeight = ref('100%');
const isExpanded = ref(true);

	const log = (msg: string) => {
	    logs.value.unshift(`[${new Date().toLocaleTimeString()}] ${msg}`);
	};

    const fetchIntegrationAgents = async () => {
        try {
            const res = await axios.get<IntegrationAgent[]>('/api/portal/agents/allowed');
            integrationAgents.value = Array.isArray(res.data) ? res.data : [];
            if (!selectedIntegrationAgentId.value && integrationAgents.value.length > 0) {
                selectedIntegrationAgentId.value = integrationAgents.value[0]!.id;
            }
        } catch {
            integrationAgents.value = [];
            log('Warn: Failed to load allowed agents for guide');
        }
    };

    const openIntegrationGuide = async () => {
        if (!config.token) {
            config.token = await resolveStoredApiKey();
        }
        showIntegrationGuide.value = true;
        void fetchIntegrationAgents();
    };

    const copyGuideCode = async () => {
        const ok = await copyToClipboard(activeIntegrationTabData.value.code);
        if (ok) {
            log(`Copied guide: ${activeIntegrationTabData.value.label}`);
            showToast('集成代码已复制到剪贴板', 'success');
        } else {
            log('Error: Copy guide failed');
            showToast('复制失败，请手动复制代码', 'error');
        }
    };

const connect = () => {
    iframeUrl.value = '/embed/chat?strict_token=1';
    log('Loading IFrame (strict token mode)...');
};

const disconnect = () => {
    iframeUrl.value = '';
    log('IFrame removed');
};

const postMsg = (type: string, payload: any = {}) => {
    if (!widgetFrame.value?.contentWindow) {
        log('Error: Widget frame not ready');
        return;
    }
    
    // In production, targetOrigin should be specific
    widgetFrame.value.contentWindow.postMessage({ type, ...payload }, '*');
    log(`TX: ${type}`);
};

const sendInit = async () => {
    if (config.authMode === 'ticket') {
        isSubmittingInit.value = true;
        try {
            log('正在调用 /api/v1/embed/tickets 签发一次性 Ticket...');
            const ticketRes = await axios.post('/api/v1/embed/tickets', {
                username: config.targetUsername.trim() || undefined,
                agent_id: config.agentId.trim() || undefined,
                expires_in: 300,
            });
            if (ticketRes.data?.code === 200 && ticketRes.data.data?.ticket) {
                const ticket = ticketRes.data.data.ticket;
                log(`Ticket 签发成功: ${ticket} (有效时长 300 秒)`);
                postMsg('INIT_CONFIG', {
                    ticket,
                    agent_id: config.agentId.trim() || undefined,
                    theme: config.theme,
                    styleVars: {
                        '--primary-color': config.primaryColor
                    }
                });
                return;
            }
            throw new Error(ticketRes.data?.message || '签发失败');
        } catch (err: any) {
            const rawDetail = err.response?.data?.detail;
            const errMsg = (typeof rawDetail === 'string' ? rawDetail : '') ||
                (typeof err.response?.data?.message === 'string' ? err.response.data.message : '') ||
                err.message ||
                'Ticket 签发异常';
            log(`Error: 签发 Ticket 失败: ${errMsg}`);
            showToast(`签发 Ticket 失败: ${errMsg}`, 'error');
        } finally {
            isSubmittingInit.value = false;
        }
        return;
    }

    const token = config.token.trim();
    if (!token) {
        log('Error: 必须填写 API Token');
        showToast('必须填写 API Token', 'error');
        return;
    }
    postMsg('INIT_CONFIG', {
        token,
        strict_token: true,
        agent_id: config.agentId,
        theme: config.theme,
        styleVars: {
            '--primary-color': config.primaryColor
        }
    });
};

const sendContext = () => {
    try {
        const payload = JSON.parse(contextPayload.value);
        postMsg('UPDATE_CONTEXT', { payload });
    } catch (e) {
        log('Error: Invalid JSON Context');
    }
};

const resetSession = async () => {
    if (config.authMode === 'ticket') {
        try {
            log('正在为重置会话申请新 Ticket...');
            const res = await axios.post('/api/v1/embed/tickets', {
                username: config.targetUsername.trim() || undefined,
                agent_id: config.agentId.trim() || undefined,
                expires_in: 300,
            });
            if (res.data?.code === 200 && res.data.data?.ticket) {
                const newTicket = res.data.data.ticket;
                log(`新 Ticket 已签发: ${newTicket}`);
                postMsg('RESET_SESSION', { ticket: newTicket });
                return;
            }
        } catch (err: any) {
            log(`Warning: 自动续签 Ticket 失败，直接发送基础重置`);
        }
    }
    postMsg('RESET_SESSION');
};

const sendCommand = () => {
    postMsg('SEND_COMMAND', { command: commandInput.value });
};

const toggleExpand = () => {
    isExpanded.value = !isExpanded.value;
    if (isExpanded.value) {
        frameWidth.value = '100%';
        frameHeight.value = '100%';
    } else {
        // Switch to "Mobile" or "Widget Bubble" simulation
        // The user complained about bad resize. Let's make it a mobile phone size.
        frameWidth.value = '375px';
        frameHeight.value = '667px'; // iPhone SE height roughly
    }
};

// Listen for UPSTREAM messages
const handleMessage = (event: MessageEvent) => {
    const data = event.data;
    if (data.source === 'nanzi-agent-embed') {
        if (data.type === 'INIT_SUCCESS') {
            const user = data.user;
            const displayName = user?.real_name || user?.user_name;
            const userLabel = displayName
                ? (user.user_name && user.real_name && user.user_name !== user.real_name ? `${user.real_name} (${user.user_name})` : displayName)
                : '';
            const successMsg = userLabel ? `已以 [${userLabel}] 身份登录就绪` : 'Token 校验通过';
            log(`RX: INIT_SUCCESS — ${successMsg}`);
            showToast(successMsg, 'success');
            return;
        }
        if (data.type === 'INIT_FAILURE') {
            const reason = data.reason === 'missing_token' ? '未提供 Token' : 'Token 无效';
            log(`RX: INIT_FAILURE — ${reason}`);
            showToast(`Token 校验失败：${reason}`, 'error');
            return;
        }
        log(`RX: ${data.type}`);
        
        if (data.type === 'NANZI_WIDGET_READY') {
            log('Widget Ready — 请配置 Token 后点击「发送 INIT_CONFIG」');
        }
    }
};

onMounted(async () => {
    window.addEventListener('message', handleMessage);
    // 接入代码需要长期有效的真实 API Key（会话令牌登出即失效），改为按需向后端获取
    if (!config.token) {
        config.token = await resolveStoredApiKey();
    }
    void fetchIntegrationAgents();
    connect();
});

onUnmounted(() => {
    window.removeEventListener('message', handleMessage);
});
</script>
