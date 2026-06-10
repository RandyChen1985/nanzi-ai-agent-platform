<template>
  <div class="h-full flex flex-col space-y-4">
    <div class="flex justify-between items-center gap-4">
      <div class="flex items-center gap-3 min-w-0">
        <h1 class="text-2xl font-bold text-gray-800 truncate">组件调试 (Widget Debugger)</h1>
        <button
          @click="showIntegrationGuide = true"
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
                <h3 class="text-sm font-bold text-gray-500 uppercase tracking-wider">1. 初始化配置</h3>
                 <div class="grid grid-cols-1 gap-2">
                    <label class="block text-xs">API Token</label>
                    <input type="text" v-model="config.token" class="w-full text-sm border-gray-300 rounded-md" placeholder="eyJ...">
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
                <button @click="sendInit" class="w-full py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700">发送 INIT_CONFIG</button>
            </section>

            <hr class="border-gray-100">

            <!-- 2. Context Injection -->
            <section class="space-y-3">
                <h3 class="text-sm font-bold text-gray-500 uppercase tracking-wider">2. 注入上下文</h3>
                <div class="grid grid-cols-1 gap-2">
                    <textarea v-model="contextPayload" rows="3" class="w-full text-xs font-mono border-gray-300 rounded-md" placeholder='{"user_dept": "IT", "current_url": "/home"}'></textarea>
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
                <p class="text-xs text-gray-500 mt-1">选择一种方式复制代码，然后在宿主系统中按需替换域名、Token 和 Agent ID。</p>
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

            <div class="border-b border-gray-100 px-4 overflow-x-auto">
              <div class="flex gap-1 min-w-max">
                <button
                  v-for="tab in integrationTabs"
                  :key="tab.id"
                  @click="activeIntegrationTab = tab.id"
                  class="px-4 py-3 text-sm font-bold border-b-2 transition-colors"
                  :class="activeIntegrationTab === tab.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-200'"
                >
                  {{ tab.label }}
                </button>
              </div>
            </div>

            <div class="flex-1 overflow-y-auto p-5 bg-gray-50">
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
                            <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 px-3 py-1.5 rounded-full bg-slate-900 text-white text-[10px] font-bold shadow">UPDATE_CONTEXT</div>
                          </template>
                        </div>
                      </div>
	                  <pre class="m-0 p-4 bg-gray-950 text-gray-100 text-xs leading-relaxed overflow-auto max-h-[52vh]"><code>{{ activeIntegrationTabData.code }}</code></pre>
	                </section>
              </div>
            </div>
          </div>
        </div>
	  </div>
	</template>
	
	<script setup lang="ts">
	import { computed, ref, reactive, onMounted, onUnmounted } from 'vue';

    interface IntegrationTab {
        id: string;
        label: string;
        title: string;
        caption: string;
        summary: string;
        points: string[];
        code: string;
    }

	const widgetFrame = ref<HTMLIFrameElement | null>(null);
	const logs = ref<string[]>([]);
	const iframeUrl = ref('');
    const showIntegrationGuide = ref(false);
    const activeIntegrationTab = ref('iframe');

const config = reactive({
    token: '',
    agentId: '',
    theme: 'light',
    primaryColor: '#3b82f6',
});

const contextPayload = ref('{\n  "user_name": "陈小龙",\n  "user_dept": "数字化转型部",\n  "user_role": "系统管理员"\n}');
	const commandInput = ref('/new');

    const integrationTabs: IntegrationTab[] = [
        {
            id: 'iframe',
            label: '快速 IFrame',
            title: '直接通过 URL 参数嵌入',
            caption: '适合内网测试、Demo 页面或快速验证 Agent 能否打开。',
            summary: '把 /embed/chat 放进 iframe，并通过 URL 参数传递 token、agent_id、theme。',
            points: ['实现最快', '便于在静态页面中验证', '生产环境不推荐在 URL 中暴露 Token'],
            code: `<iframe
  src="https://your-yunshu-domain/embed/chat?token=YOUR_TOKEN&agent_id=sys-agent-chatbi&theme=light"
  width="100%"
  height="640"
  frameborder="0"
  style="border:0;border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,.12);"
></iframe>`,
        },
        {
            id: 'postmessage',
            label: 'PostMessage 推荐',
            title: '安全初始化和双向通信',
            caption: '推荐生产使用：iframe src 不带敏感 token，等组件 ready 后发送 INIT_CONFIG。',
            summary: '宿主页面监听 YUNSHU_WIDGET_READY，再通过 postMessage 发送鉴权、智能体、用户和页面上下文。',
            points: ['Token 不进入 URL', '支持用户身份和业务上下文注入', '可以响应组件 resize、过期、连接状态事件'],
            code: `<iframe
  id="yunshu-agent-frame"
  src="https://your-yunshu-domain/embed/chat?instance_id=ops-assistant"
  width="100%"
  height="640"
  frameborder="0"
></iframe>

<script>
const frame = document.getElementById('yunshu-agent-frame');
const targetOrigin = 'https://your-yunshu-domain';

window.addEventListener('message', (event) => {
  if (event.origin !== targetOrigin) return;
  const data = event.data || {};
  if (data.source !== 'yunshu-agent-embed') return;

  if (data.type === 'YUNSHU_WIDGET_READY') {
    frame.contentWindow.postMessage({
      type: 'INIT_CONFIG',
      instance_id: 'ops-assistant',
      token: 'YOUR_TOKEN',
      agent_id: 'sys-agent-chatbi',
      theme: 'light',
      user_info: {
        user_id: 'U10001',
        real_name: '张三',
        role: 'operator'
      },
      page_info: {
        current_page: '容量看板',
        system: 'ops-portal'
      },
      styleVars: {
        '--primary-color': '#1677ff'
      }
    }, targetOrigin);
  }

  if (data.type === 'REQUEST_RESIZE') {
    frame.style.height = data.expanded ? '720px' : '64px';
  }
});
<\/script>`,
        },
        {
            id: 'floating',
            label: '悬浮按钮模式',
            title: '右下角悬浮助手',
            caption: '适合接入已有业务系统，默认收起，点击后展开完整对话窗口。',
            summary: '宿主系统控制 iframe 容器的展开/收起，让助手像客服入口一样固定在页面右下角。',
            points: ['不占用业务页面主布局', '适合门户、控制台、报表页', '移动端可以切换为全屏覆盖'],
            code: `<div id="yunshu-widget-shell" class="yunshu-widget-shell collapsed">
  <button id="yunshu-widget-toggle" class="yunshu-widget-toggle">AI</button>
  <iframe
    id="yunshu-agent-frame"
    src="https://your-yunshu-domain/embed/chat?instance_id=floating-ai"
    frameborder="0"
  ></iframe>
</div>

<style>
.yunshu-widget-shell {
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
}
.yunshu-widget-shell.collapsed {
  width: 56px;
  height: 56px;
  border-radius: 999px;
}
.yunshu-widget-shell iframe {
  width: 100%;
  height: 100%;
  border: 0;
}
.yunshu-widget-shell.collapsed iframe {
  display: none;
}
.yunshu-widget-toggle {
  display: none;
  width: 100%;
  height: 100%;
  border: 0;
  border-radius: 999px;
  background: #1677ff;
  color: #fff;
  font-weight: 800;
}
.yunshu-widget-shell.collapsed .yunshu-widget-toggle {
  display: block;
}
</style>

<script>
const shell = document.getElementById('yunshu-widget-shell');
document.getElementById('yunshu-widget-toggle').onclick = () => {
  shell.classList.remove('collapsed');
};
<\/script>`,
        },
        {
            id: 'context',
            label: '多实例/上下文',
            title: '同步业务上下文和多实例隔离',
            caption: '适合页面上有多个助手，或需要让 Agent 感知当前业务对象的场景。',
            summary: '为每个组件设置 instance_id，并在页面状态变化时发送 UPDATE_CONTEXT 或 SEND_COMMAND。',
            points: ['多 iframe 不串消息', 'Agent 可读取当前页面、工单、资产等上下文', '业务按钮可以直接触发组件内部指令'],
            code: `const frame = document.getElementById('yunshu-agent-frame');
const targetOrigin = 'https://your-yunshu-domain';
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
      current_page: 'ticket-detail',
      ticket_id: ticket.id,
      title: ticket.title,
      priority: ticket.priority,
      owner_dept: ticket.ownerDept
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

    const activeIntegrationTabData = computed<IntegrationTab>(() => {
        return integrationTabs.find((tab) => tab.id === activeIntegrationTab.value) || integrationTabs[0]!;
    });

// Simulate Host Size control
const frameWidth = ref('100%');
const frameHeight = ref('100%');
const isExpanded = ref(true);

	const log = (msg: string) => {
	    logs.value.unshift(`[${new Date().toLocaleTimeString()}] ${msg}`);
	};

    const copyGuideCode = async () => {
        try {
            await navigator.clipboard.writeText(activeIntegrationTabData.value.code);
            log(`Copied guide: ${activeIntegrationTabData.value.label}`);
        } catch {
            log('Error: Copy guide failed');
        }
    };

const connect = () => {
    // Start without params to simulate full handshake
    iframeUrl.value = '/embed/chat'; 
    log('Loading IFrame...');
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

const sendInit = () => {
    postMsg('INIT_CONFIG', {
        token: config.token || 'TEST_TOKEN', 
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

const resetSession = () => {
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
    if (data.source === 'yunshu-agent-embed') {
        log(`RX: ${data.type}`);
        
        if (data.type === 'YUNSHU_WIDGET_READY') {
            log('Widget Ready! Sending Init...');
            // Optional: Auto init not always desired in debug if we want to test manual init
            // But let's keep it for convenience or add a checkbox "Auto Init" later?
            // For now, let's remove auto-init to let user click button as they might want to change config first.
            // Or only auto init if token is present?
            // User said "how to fill token", so they want manual control.
            // I'll remove auto-setTimeout init so they can fill form first.
            log('(Auto-init disabled, please click Send INIT_CONFIG)');
        }
    }
};

onMounted(() => {
    window.addEventListener('message', handleMessage);
    // Auto-load token from current user session
    const storedKey = localStorage.getItem('api_key');
    if (storedKey) {
        config.token = storedKey;
    }
    connect();
});

onUnmounted(() => {
    window.removeEventListener('message', handleMessage);
});
</script>
