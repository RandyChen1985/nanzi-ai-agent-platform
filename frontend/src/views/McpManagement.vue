<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from '../utils/axios'
import McpServerRegistry from '../components/system/McpServerRegistry.vue'
import McpFlowGuideBanner from '../components/mcp/McpFlowGuideBanner.vue'
import McpAuditLogTab from '../components/mcp/McpAuditLogTab.vue'
import { useUser } from '../composables/useUser'

const props = withDefaults(defineProps<{
  /** 仅展示「我的 MCP」，用于个人中心（无需 menu:mcp_management） */
  personalOnly?: boolean
}>(), {
  personalOnly: false,
})

const router = useRouter()
const { isAdmin } = useUser()
const activeScope = ref<'global' | 'personal' | 'audit'>(props.personalOnly ? 'personal' : 'global')
const registryRef = ref<InstanceType<typeof McpServerRegistry> | null>(null)

// 平台 MCP 与我的 MCP 数量计数
const serverCounts = ref<{
  global: number
  personal: number
}>({
  global: 0,
  personal: 0,
})

const fetchServerCounts = async () => {
  try {
    const [globalRes, personalRes] = await Promise.allSettled([
      axios.get('/api/portal/mcp/servers', { params: { scope: 'global' } }),
      axios.get('/api/portal/mcp/servers', { params: { scope: 'personal' } }),
    ])
    if (globalRes.status === 'fulfilled' && Array.isArray(globalRes.value.data)) {
      serverCounts.value.global = globalRes.value.data.length
    }
    if (personalRes.status === 'fulfilled' && Array.isArray(personalRes.value.data)) {
      serverCounts.value.personal = personalRes.value.data.length
    }
  } catch (err) {
    console.error('获取 MCP 计数失败', err)
  }
}

const handleServerCountChanged = (payload: { scope: 'global' | 'personal'; count: number }) => {
  if (payload.scope === 'global' || payload.scope === 'personal') {
    serverCounts.value[payload.scope] = payload.count
  }
}

onMounted(() => {
  void fetchServerCounts()
})

// 流程指引横幅状态与持久化
const MCP_FLOW_GUIDE_KEY = 'nanzi_mcp_flow_guide_dismissed'
const showMcpFlowGuide = ref(localStorage.getItem(MCP_FLOW_GUIDE_KEY) !== 'true')

const handleCloseFlowGuide = () => {
  showMcpFlowGuide.value = false
}

const handleDismissFlowGuide = () => {
  showMcpFlowGuide.value = false
  localStorage.setItem(MCP_FLOW_GUIDE_KEY, 'true')
}

const restoreMcpFlowGuide = () => {
  localStorage.removeItem(MCP_FLOW_GUIDE_KEY)
  showMcpFlowGuide.value = true
}

// 规范与全流程指引大弹窗
const showHelp = ref(false)
const activeHelpTab = ref<'flow' | 'protocols' | 'security'>('flow')

const handleBannerAction = (action: 'add' | 'marketplace') => {
  if (action === 'add') {
    registryRef.value?.openAddModal('manual')
  } else if (action === 'marketplace') {
    registryRef.value?.openAddModal('json')
  }
}
</script>

<template>
  <div
    class="flex flex-col space-y-4"
    :class="personalOnly ? 'min-h-0' : 'h-full overflow-hidden'"
  >
    <!-- Header：标题 + 徽章一行，说明另起一行，避免右栏 max-width 把末尾「生态」挤断 -->
    <div class="flex flex-shrink-0 flex-col gap-1">
      <div class="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
        <h1
          class="font-bold tracking-tight text-gray-900 dark:text-white"
          :class="personalOnly ? 'text-lg sm:text-xl' : 'text-xl sm:text-2xl'"
        >
          {{ personalOnly ? '我的 MCP' : 'MCP 工具集' }}
        </h1>

        <!-- ? 规范与指引大弹窗按钮 -->
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-full border border-gray-200 bg-white text-indigo-600 shadow-sm transition-colors hover:border-indigo-300 hover:bg-indigo-50 dark:border-gray-700 dark:bg-gray-800 dark:text-indigo-400 cursor-pointer"
          title="MCP 设计规范与全流程指引"
          @click="showHelp = true"
        >
          <span class="text-sm font-bold">?</span>
        </button>

        <!-- 恢复顶部指引常驻按钮 -->
        <button
          v-if="!showMcpFlowGuide && !personalOnly"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50/80 px-2.5 py-1 text-xs font-medium text-indigo-700 shadow-2xs transition-colors hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950/40 dark:text-indigo-300 cursor-pointer"
          title="重新展开 MCP 全流程指引"
          @click="restoreMcpFlowGuide"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span class="whitespace-nowrap">显示指引</span>
        </button>

        <span
          v-if="!personalOnly"
          class="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300"
        >
          Model Context Protocol
        </span>
        <span
          v-else
          class="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
        >
          个人私有
        </span>
      </div>
      <p class="mcp-page-desc text-xs leading-relaxed text-gray-500 dark:text-gray-400 sm:text-sm">
        {{
          personalOnly
            ? '登记并管理仅对自己可见的 MCP，可在对话中挂载使用'
            : '接入并管理外部 MCP 远程服务（SSE / Streamable HTTP），自动识别工具集并无缝绑定至智能体生态'
        }}
      </p>
    </div>

    <!-- MCP 5 步全生命周期指引横幅 -->
    <div v-if="showMcpFlowGuide && !personalOnly" class="flex-shrink-0">
      <McpFlowGuideBanner
        @action="handleBannerAction"
        @close="handleCloseFlowGuide"
        @dismiss="handleDismissFlowGuide"
      />
    </div>

    <!-- Scope Tab：个人中心模式下隐藏平台/个人切换 -->
    <div v-if="!personalOnly" class="flex flex-shrink-0 items-center border-b border-gray-200 dark:border-gray-800">
      <button
        id="tab-global-mcp"
        type="button"
        @click="activeScope = 'global'"
        class="flex cursor-pointer items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors"
        :class="activeScope === 'global' ? 'border-blue-600 font-bold text-blue-600 dark:border-blue-400 dark:text-blue-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064" />
        </svg>
        <span>平台 MCP</span>
        <span
          class="inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums transition-colors"
          :class="activeScope === 'global' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'"
        >
          {{ serverCounts.global }}
        </span>
      </button>
      <button
        id="tab-personal-mcp"
        type="button"
        @click="activeScope = 'personal'"
        class="flex cursor-pointer items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors"
        :class="activeScope === 'personal' ? 'border-emerald-600 font-bold text-emerald-600 dark:border-emerald-400 dark:text-emerald-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
        <span>我的 MCP</span>
        <span
          class="inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums transition-colors"
          :class="activeScope === 'personal' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'"
        >
          {{ serverCounts.personal }}
        </span>
      </button>
      <button
        v-if="isAdmin"
        id="tab-audit-logs"
        type="button"
        @click="activeScope = 'audit'"
        class="flex cursor-pointer items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors"
        :class="activeScope === 'audit' ? 'border-indigo-600 font-bold text-indigo-600 dark:border-indigo-400 dark:text-indigo-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        审计日志
      </button>
    </div>

    <!-- 主体区域：个人中心随页面滚动；控制台保留定高裁剪 -->
    <div :class="personalOnly ? 'min-h-[28rem]' : 'min-h-0 flex-1 overflow-hidden'">
      <McpAuditLogTab v-if="activeScope === 'audit' && isAdmin" />
      <McpServerRegistry
        v-else
        ref="registryRef"
        :key="activeScope"
        :scope="activeScope === 'audit' ? 'global' : activeScope"
        @server-count-changed="handleServerCountChanged"
      />
    </div>

    <!-- MCP 设计规范与全流程指引 Modal -->
    <div v-if="showHelp" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showHelp = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-indigo-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center shadow-md shadow-indigo-500/20" style="background-color: #4f46e5; color: #ffffff;">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">MCP 工具集设计规范与全流程指引</h2>
               <p class="text-xs text-gray-500 font-medium mt-0.5">Model Context Protocol 开放生态集成，从服务接入、自发现探活到多智能体装配调用。</p>
             </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              v-if="!showMcpFlowGuide"
              type="button"
              @click="restoreMcpFlowGuide"
              class="inline-flex items-center gap-1 text-xs font-medium text-indigo-700 bg-indigo-100/70 hover:bg-indigo-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              <span>恢复顶部流程提示</span>
            </button>
            <button @click="showHelp = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        <!-- Tabs -->
        <div class="flex border-b border-gray-200 bg-white px-6">
           <button 
             v-for="tab in ['flow', 'protocols', 'security']" 
             :key="tab"
             @click="activeHelpTab = tab as any"
             class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
             :class="activeHelpTab === tab ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
           >
             {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                tab === 'protocols' ? '连接协议与生态规范 (Protocols)' : '安全隔离与作用域 (Scope & Security)' }}
           </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
           <!-- Tab 1: Workflow Flow -->
           <div v-if="activeHelpTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
              <div class="bg-gradient-to-r from-indigo-50 to-blue-50 border-l-4 border-indigo-600 p-4 rounded-r-xl shadow-2xs">
                 <h3 class="font-bold text-indigo-900 mb-1">MCP 5 步全生命周期接入体系</h3>
                 <p class="text-xs text-indigo-700 leading-relaxed">
                    Model Context Protocol (MCP) 是标准化的外部工具开放协议。接入服务后平台自动发起 tools/list 自发现，可在线沙箱调试并在智能体中心一键挂载。
                 </p>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                 <!-- Step 1 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                          <h4 class="font-bold text-gray-900 text-sm">服务登记与生态安装</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          支持接入 SSE 或 Streamable HTTP 远程服务，或粘贴包含 URL 的 JSON 配置；可直接从生态市场一键安装官方精选服务。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                       <button
                          type="button"
                          @click="showHelp = false; registryRef?.openAddModal('manual')"
                          class="text-xs text-indigo-600 hover:text-indigo-800 font-bold cursor-pointer"
                       >
                          新增服务 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 2 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                          <h4 class="font-bold text-gray-900 text-sm">探活发现与工具同步</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          系统自动发起 tools/list 协议握手，提取工具名称、描述与 JSON Schema 入参定义；支持随时点击刷新保持最新。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       服务列表中点击「刷新工具」
                    </div>
                 </div>

                 <!-- Step 3 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-teal-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                          <h4 class="font-bold text-gray-900 text-sm">在线测试与参数调试</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          在服务卡片中点击工具「测试」按钮进入内置调试台，输入实参实时触发 MCP 执行并观测原始报文返回。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       服务卡片中点击「测试」按钮
                    </div>
                 </div>

                 <!-- Step 4 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-purple-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                          <h4 class="font-bold text-gray-900 text-sm">范围隔离与权限分配</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          区分「平台公开 MCP」（全员共享，管理员维护）与「我的 MCP」（个人私有专属）；在角色管理中细粒度下发维护权限。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                       <button
                          type="button"
                          @click="showHelp = false; router.push('/dashboard/roles')"
                          class="text-xs text-purple-600 hover:text-purple-800 font-medium cursor-pointer"
                       >
                          前往角色管理 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 5 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between md:col-span-2">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-amber-600 text-white flex items-center justify-center text-xs font-bold">5</span>
                          <h4 class="font-bold text-gray-900 text-sm">智能体挂载与协同调用</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          前往「智能体中心」在目标智能体版本装配中勾选已启用的 MCP 服务；智能体在对话中自主调度 MCP 完成复杂操作。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                       <button
                          type="button"
                          @click="showHelp = false; router.push('/dashboard/agent-management')"
                          class="text-xs text-amber-600 hover:text-amber-800 font-medium cursor-pointer"
                       >
                          前往智能体中心挂载 &rarr;
                       </button>
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 2: Protocols -->
           <div v-else-if="activeHelpTab === 'protocols'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">MCP 协议连接模式与生态规范</h4>
                 <div class="space-y-3 text-xs">
                    <div class="p-3.5 bg-indigo-50/60 rounded-xl border border-indigo-100 space-y-1">
                       <span class="font-bold text-indigo-900 text-sm">1. SSE 远程服务传输（Server-Sent Events）</span>
                       <p class="text-gray-600 leading-relaxed">适用于部署在独立 Docker 容器或云服务上的 MCP Server（例如 <code>http://mcp-server:8000/sse</code>）。支持自定义 Header 鉴权与长连接流式响应。</p>
                    </div>
                    <div class="p-3.5 bg-blue-50/60 rounded-xl border border-blue-100 space-y-1">
                       <span class="font-bold text-blue-900 text-sm">2. Streamable HTTP 远程服务传输</span>
                       <p class="text-gray-600 leading-relaxed">适用于提供 MCP HTTP 端点的云服务或网关（例如 <code>https://example.com/mcp</code>）。系统会自动探测协议，并通过 HTTP 请求完成初始化、工具发现和调用；支持自定义 Header 鉴权。</p>
                    </div>
                    <div class="p-3.5 bg-purple-50/60 rounded-xl border border-purple-100 space-y-1">
                       <span class="font-bold text-purple-900 text-sm">3. JSON 配置粘贴</span>
                       <p class="text-gray-600 leading-relaxed">支持粘贴 Claude Desktop / VSCode 的标准 <code>mcpServers</code> JSON 配置，提取其中第一个包含 URL 的服务地址与请求头；纯 Stdio 配置（仅 command/args）暂不支持。</p>
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 3: Security & Scope -->
           <div v-else-if="activeHelpTab === 'security'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">安全隔离与作用域（Scope）规则</h4>
                 <div class="space-y-3 text-xs">
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">平台 MCP（Global Scope）</span>
                       <p class="text-gray-600">全局公共服务，由系统管理员统一维护。所有获得智能体使用权限的用户均可基于智能体调用该服务中的已发布工具。</p>
                    </div>
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">我的 MCP（Personal Scope）</span>
                       <p class="text-gray-600">个人私有专属服务，服务名称带专属前缀隔离，仅限本人在个人会话与私有智能体中挂载，确保私密密钥不外泄。</p>
                    </div>
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">工具级发布状态（Publish Status）</span>
                       <p class="text-gray-600">服务端探测到的工具默认需处于「已发布」状态方可在对话中被大模型检索和调用；支持一键下线特定高危工具。</p>
                    </div>
                 </div>
              </div>
           </div>
        </div>
      </div>
    </div>
  </div>
</template>
