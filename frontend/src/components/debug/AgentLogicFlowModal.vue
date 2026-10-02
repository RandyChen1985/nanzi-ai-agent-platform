<script setup lang="ts">
defineProps<{
  visible: boolean;
}>();

const emit = defineEmits<{
  (event: "close"): void;
}>();
</script>

<template>
  <!-- Modal: Logic Flow SVG -->
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div
        class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl overflow-hidden animate-fade-in-up"
      >
        <div
          class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50/50"
        >
          <h3 class="font-bold text-gray-800 text-lg flex items-center">
            <svg
              class="w-5 h-5 text-blue-600 mr-2"
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
            智能体运行逻辑架构 (Agent Execution Logic)
          </h3>
          <button
            @click="emit('close')"
            class="text-gray-400 hover:text-gray-600 p-2 rounded-full hover:bg-gray-100 transition-all"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <div class="p-10 bg-white flex justify-center overflow-x-auto">
          <!-- SVG Architecture Diagram (Final Refined Version) -->
          <svg
            width="800"
            height="420"
            viewBox="0 0 800 420"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <!-- Definitions -->
            <defs>
              <marker
                id="arrowhead"
                markerWidth="10"
                markerHeight="7"
                refX="0"
                refY="3.5"
                orient="auto"
              >
                <polygon points="0 0, 10 3.5, 0 7" fill="#94a3b8" />
              </marker>
              <linearGradient id="blueGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#3b82f6" />
                <stop offset="100%" stop-color="#2563eb" />
              </linearGradient>
            </defs>

            <!-- Main Path: User -> Intent -> Agent Service -> Rendering -> Audit -->

            <!-- 1. User -->
            <rect
              x="20"
              y="160"
              width="110"
              height="50"
              rx="10"
              fill="#f8fafc"
              stroke="#e2e8f0"
              stroke-width="2"
            />
            <text
              x="75"
              y="185"
              text-anchor="middle"
              fill="#1e293b"
              font-weight="bold"
              font-size="13"
            >
              用户提问
            </text>
            <text
              x="75"
              y="200"
              text-anchor="middle"
              fill="#94a3b8"
              font-size="9"
            >
              User Query
            </text>
            <path
              d="M130 185 H160"
              stroke="#94a3b8"
              stroke-width="2"
              marker-end="url(#arrowhead)"
            />

            <!-- 2. Intent Recognition -->
            <rect
              x="170"
              y="160"
              width="130"
              height="50"
              rx="10"
              fill="#eff6ff"
              stroke="#3b82f6"
              stroke-width="2"
            />
            <text
              x="235"
              y="185"
              text-anchor="middle"
              fill="#1e3a8a"
              font-weight="bold"
              font-size="13"
            >
              意图识别
            </text>
            <text
              x="235"
              y="200"
              text-anchor="middle"
              fill="#3b82f6"
              font-size="9"
            >
              Intent Engine
            </text>
            <path
              d="M300 185 H350"
              stroke="#3b82f6"
              stroke-width="2"
              marker-end="url(#arrowhead)"
            />

            <!-- 3. Agent Service (Detailed ChatBI Path) -->
            <rect
              x="360"
              y="135"
              width="160"
              height="100"
              rx="12"
              fill="url(#blueGrad)"
              stroke="#1d4ed8"
              stroke-width="2"
              opacity="0.9"
            />
            <text
              x="440"
              y="175"
              text-anchor="middle"
              fill="white"
              font-weight="bold"
              font-size="15"
            >
              Agent Service
            </text>
            <text
              x="440"
              y="195"
              text-anchor="middle"
              fill="#bfdbfe"
              font-size="10"
            >
              ChatBI 核心编排
            </text>
            <text
              x="440"
              y="212"
              text-anchor="middle"
              fill="white"
              fill-opacity="0.6"
              font-size="9"
              font-family="monospace"
            >
              Tool-Calling
            </text>

            <!-- Metadata (Above) -->
            <path
              d="M440 135 V105"
              stroke="#94a3b8"
              stroke-width="1.5"
              stroke-dasharray="2 2"
              marker-end="url(#arrowhead)"
            />
            <rect
              x="380"
              y="50"
              width="120"
              height="45"
              rx="8"
              fill="#f0f9ff"
              stroke="#0ea5e9"
              stroke-width="1.5"
            />
            <text
              x="440"
              y="72"
              text-anchor="middle"
              fill="#0369a1"
              font-weight="bold"
              font-size="11"
            >
              Metadata Service
            </text>
            <text
              x="440"
              y="85"
              text-anchor="middle"
              fill="#0ea5e9"
              font-size="9"
            >
              元数据/Schema检索
            </text>

            <!-- External API (Below) -->
            <path
              d="M440 235 V265"
              stroke="#94a3b8"
              stroke-width="1.5"
              marker-end="url(#arrowhead)"
            />
            <rect
              x="370"
              y="275"
              width="140"
              height="50"
              rx="8"
              fill="#fff7ed"
              stroke="#f59e0b"
              stroke-width="1.5"
            />
            <text
              x="440"
              y="298"
              text-anchor="middle"
              fill="#9a3412"
              font-weight="bold"
              font-size="11"
            >
              外部数据接口 (8000)
            </text>
            <text
              x="440"
              y="312"
              text-anchor="middle"
              fill="#f59e0b"
              font-size="9"
            >
              SQL执行 / 获取结果
            </text>

            <!-- 4. Secondary Path: RAG & Chat (Dashed Branches Below) -->
            <path
              d="M235 210 V360 H360"
              stroke="#94a3b8"
              stroke-width="1.5"
              stroke-dasharray="4 3"
              marker-end="url(#arrowhead)"
            />
            <rect
              x="370"
              y="340"
              width="140"
              height="40"
              rx="8"
              fill="#f9fafb"
              stroke="#d1d5db"
              stroke-width="1.5"
              stroke-dasharray="4 2"
            />
            <text
              x="440"
              y="360"
              text-anchor="middle"
              fill="#64748b"
              font-weight="bold"
              font-size="11"
            >
              RAG / 基础闲聊
            </text>
            <text
              x="440"
              y="372"
              text-anchor="middle"
              fill="#9ca3af"
              font-size="8"
            >
              规划中能力 (Extension)
            </text>
            <path
              d="M510 360 H625 V245"
              stroke="#94a3b8"
              stroke-width="1.5"
              stroke-dasharray="4 3"
            />

            <!-- 5. Rendering -->
            <path
              d="M520 185 H570"
              stroke="#22c55e"
              stroke-width="2"
              marker-end="url(#arrowhead)"
            />
            <rect
              x="580"
              y="155"
              width="130"
              height="60"
              rx="12"
              fill="#f0fdf4"
              stroke="#22c55e"
              stroke-width="2"
            />
            <text
              x="645"
              y="185"
              text-anchor="middle"
              fill="#14532d"
              font-weight="bold"
              font-size="14"
            >
              结果合成渲染
            </text>
            <text
              x="645"
              y="200"
              text-anchor="middle"
              fill="#22c55e"
              font-size="10"
            >
              Markdown/ECharts
            </text>

            <!-- 6. Audit -->
            <path
              d="M710 185 H740"
              stroke="#94a3b8"
              stroke-width="2"
              marker-end="url(#arrowhead)"
            />
            <rect
              x="750"
              y="160"
              width="40"
              height="50"
              rx="8"
              fill="#fafafa"
              stroke="#d1d5db"
              stroke-width="1"
            />
            <text
              x="770"
              y="185"
              text-anchor="middle"
              fill="#4b5563"
              font-weight="bold"
              font-size="10"
            >
              审计
            </text>
            <text
              x="770"
              y="198"
              text-anchor="middle"
              fill="#9ca3af"
              font-size="8"
            >
              Trace
            </text>
          </svg>
        </div>
        <div
          class="px-8 py-6 bg-gray-50 border-t border-gray-100 flex items-start space-x-4"
        >
          <div class="bg-blue-100 p-2 rounded-lg">
            <svg
              class="w-5 h-5 text-blue-600"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <div class="text-xs text-gray-500 leading-relaxed">
            <p class="font-bold text-gray-700 mb-1">
              设计说明 (Architectural Notes):
            </p>
            1. <b>核心链路 (实线)</b>：专注于 ChatBI
            数据查询，支持元数据自动注入与外部 SQL (8000端口) 代理执行。<br />
            2. <b>扩展链路 (虚线)</b>：预留 RAG
            知识库与通用对话分支，统一由意图引擎分发，最终汇聚至渲染引擎。<br />
            3. <b>元数据与外部执行</b>：体现了 Agent
            在执行过程中的上下游依赖关系，确保查数逻辑的闭环。
          </div>
        </div>
      </div>
    </div>
  </Teleport>

</template>

