<script setup lang="ts">
/**
 * 案例集检索测试弹窗（模拟器）。
 *
 * 以与 ChatBI 运行时完全一致的链路在经验库（ai_chatbi_examples）中检索相似案例，
 * 支持临时覆盖检索模式 / Top K / 相似度阈值 / 向量权重，供管理员验证样例集命中
 * 情况与调参效果。只读操作，不落库、不产生副作用。
 *
 * 使用方式：父组件 <ExampleSearchTestModal ref="searchTestModalRef" />，
 * 通过 searchTestModalRef.open() 打开。
 */
import { ref, computed } from "vue";
import axios from "@/utils/axios";
import { useToast } from "../../composables/useToast";

const { showToast } = useToast();

const visible = ref(false);
const open = () => {
  visible.value = true;
};
defineExpose({ open });

// ---- 查询与结果状态 ----
const testQuery = ref("");
const testLoading = ref(false);
const testResult = ref<SearchTestResult | null>(null);

// ---- 高级参数（仅本次测试生效） ----
const showAdvancedSettings = ref(false);
const tempProvider = ref<string>("default");
const tempTopK = ref<number>(5);
const tempThreshold = ref<number>(0.4);
const tempVectorWeight = ref<number>(0.5);
const showVectorWeight = computed(() => tempProvider.value === "ragflow");

interface ExampleHitItem {
  id?: number;
  question?: string;
  sql?: string;
  context_summary?: string;
  dataset_name?: string;
  trace_id?: string;
  similarity?: number;
}

interface SearchTestResult {
  found: boolean;
  provider: string;
  count: number;
  items: ExampleHitItem[];
  logs: string[];
  elapsed_ms: number;
}

const providerLabel = (provider: string): string => {
  if (provider === "local") return "local · Redis 向量检索";
  if (provider === "ragflow") return "ragflow · RAGFlow";
  return provider || "unknown";
};

const handleTestRetrieval = async () => {
  const query = testQuery.value.trim();
  if (!query) {
    showToast("请输入要检索的用户问题", "warning");
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  try {
    const res = await axios.post("/api/portal/examples/search-test", {
      query,
      metadata_provider: tempProvider.value,
      top_k: tempTopK.value,
      similarity_threshold: tempThreshold.value,
      vector_weight: tempVectorWeight.value,
    });
    if (res.data.code === 200) {
      testResult.value = res.data.data;
    } else {
      showToast(res.data.message || "检索失败", "error");
    }
  } catch (error) {
    showToast("检索失败，请检查后端服务", "error");
  } finally {
    testLoading.value = false;
  }
};

const close = () => {
  visible.value = false;
};

const formatSimilarity = (sim?: number): string => {
  if (sim === undefined || sim === null) return "—";
  return sim.toFixed(2);
};

const hitCountText = (count: number): string => `命中 ${count} 条相似案例`;

const isHitLog = (log: string): boolean =>
  String(log).includes("[HIT]") || String(log).includes("[MISS]") || String(log).includes("[ERROR]");

const isNeutralLog = (log: string): boolean =>
  String(log).includes("[WARN]") || String(log).includes("[FALLBACK]");
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div class="flex h-[88vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-gray-100 bg-white shadow-2xl animate-fade-in-up">
      <!-- Header -->
      <div class="flex items-center justify-between border-b border-gray-100 bg-amber-50/30 p-6">
        <div class="flex items-center gap-3">
          <div class="flex h-10 w-10 items-center justify-center rounded-lg border border-amber-200 bg-amber-100 text-amber-600">
            <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
          <div>
            <h2 class="text-xl font-bold text-gray-900">案例集检索模拟器</h2>
            <p class="text-xs font-medium text-gray-500">模拟 ChatBI 经验库检索链路，验证样例集命中情况与调参效果（只读测试，不产生副作用）。</p>
          </div>
        </div>
        <button type="button" class="cursor-pointer text-gray-400 transition-colors hover:text-gray-600" title="关闭" @click="close">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="flex flex-1 flex-col overflow-hidden">
        <!-- Search Bar -->
        <div class="flex flex-col gap-4 border-b border-gray-100 bg-white p-6">
          <div class="flex gap-4">
            <input
              v-model="testQuery"
              type="text"
              class="flex-1 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 transition-all focus:outline-none focus:ring-2 focus:ring-amber-500"
              placeholder="输入用户问题，例如：'查询上海机房本月的 PUE'..."
              @keyup.enter="handleTestRetrieval"
            />
            <button
              type="button"
              class="flex items-center gap-2 rounded-xl bg-amber-500 px-6 py-3 font-bold text-white shadow-lg shadow-amber-500/20 transition-all hover:bg-amber-600 disabled:opacity-50"
              :disabled="testLoading || !testQuery"
              @click="handleTestRetrieval"
            >
              <svg v-if="testLoading" class="h-5 w-5 animate-spin text-white" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span v-else>执行检索</span>
            </button>
          </div>

          <!-- 高级参数折叠触发 -->
          <div class="flex items-center">
            <button
              type="button"
              class="flex cursor-pointer items-center gap-1 text-xs font-medium text-gray-500 transition-colors hover:text-amber-600"
              @click="showAdvancedSettings = !showAdvancedSettings"
            >
              <svg
                class="h-4 w-4 transition-transform duration-200"
                :class="{ 'rotate-90': showAdvancedSettings }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              高级参数配置 (仅临时测试)
            </button>
          </div>

          <!-- 高级参数面板 -->
          <div
            v-if="showAdvancedSettings"
            class="grid grid-cols-1 gap-4 rounded-xl border border-gray-100 bg-gray-50/80 p-4 text-xs transition-all duration-300 md:grid-cols-2"
          >
            <!-- Provider -->
            <div class="flex items-center gap-3">
              <span class="w-24 shrink-0 font-medium text-gray-600">检索模式:</span>
              <select
                v-model="tempProvider"
                class="flex-1 cursor-pointer rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-amber-500"
              >
                <option value="default">跟随系统配置</option>
                <option value="local">本地 Redis 向量检索 (local)</option>
                <option value="ragflow">知识库检索模式 (ragflow)</option>
              </select>
            </div>

            <!-- Top K -->
            <div class="flex items-center gap-3">
              <span class="w-24 shrink-0 font-medium text-gray-600">Top K 数量:</span>
              <div class="flex flex-1 items-center gap-2">
                <input v-model.number="tempTopK" type="range" min="1" max="20" step="1" class="flex-1 cursor-pointer accent-amber-500">
                <span class="w-8 rounded border border-gray-200 bg-white px-1 py-0.5 text-right font-mono font-semibold text-gray-700">{{ tempTopK }}</span>
              </div>
            </div>

            <!-- Similarity Threshold -->
            <div class="flex items-center gap-3">
              <span class="w-24 shrink-0 font-medium text-gray-600">相似度阈值:</span>
              <div class="flex flex-1 items-center gap-2">
                <input v-model.number="tempThreshold" type="range" min="0" max="1" step="0.05" class="flex-1 cursor-pointer accent-amber-500">
                <span class="w-8 rounded border border-gray-200 bg-white px-1 py-0.5 text-right font-mono font-semibold text-gray-700">{{ tempThreshold.toFixed(2) }}</span>
              </div>
            </div>

            <!-- Vector Weight (仅 ragflow) -->
            <div v-if="showVectorWeight" class="flex items-center gap-3">
              <span class="w-24 shrink-0 font-medium text-gray-600">向量检索权重:</span>
              <div class="flex flex-1 items-center gap-2">
                <input v-model.number="tempVectorWeight" type="range" min="0" max="1" step="0.05" class="flex-1 cursor-pointer accent-amber-500">
                <span class="w-8 rounded border border-gray-200 bg-white px-1 py-0.5 text-right font-mono font-semibold text-gray-700">{{ tempVectorWeight.toFixed(2) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Results -->
        <div class="flex flex-1 overflow-hidden bg-gray-50">
          <!-- Left Column: Hit List -->
          <div class="flex-1 overflow-y-auto border-r border-gray-200 p-6">
            <div v-if="!testResult && !testLoading" class="flex h-full flex-col items-center justify-center text-gray-400 opacity-50">
              <svg class="mb-4 h-16 w-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 16l2.879-2.879m0 0a3 3 0 104.243-4.242 3 3 0 00-4.243 4.242zM21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p>请输入用户问题开始测试</p>
            </div>

            <div v-else-if="testResult" class="space-y-6">
              <!-- Status Banner -->
              <div v-if="testResult.found" class="flex items-start gap-3 rounded-lg border border-green-200 bg-green-50 p-4">
                <svg class="mt-0.5 h-5 w-5 shrink-0 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <h4 class="text-sm font-bold text-green-800">检索成功 (Hit)</h4>
                  <p class="mt-1 text-xs text-green-700">
                    Provider: <b class="uppercase">{{ providerLabel(testResult.provider) }}</b> |
                    {{ hitCountText(testResult.count) }} | 检索耗时 {{ testResult.elapsed_ms }}ms
                  </p>
                </div>
              </div>
              <div v-else class="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4">
                <svg class="mt-0.5 h-5 w-5 shrink-0 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <span class="font-bold text-red-800">未找到相关案例 (Miss)</span>
                  <p class="mt-1 text-xs text-red-600">请查看右侧「执行日志 (Trace)」：含检索模式、召回数、阈值过滤与关键词兜底详情。</p>
                </div>
              </div>

              <!-- Hit Cards -->
              <div v-if="testResult.found && testResult.items.length" class="space-y-3">
                <div
                  v-for="(item, idx) in testResult.items"
                  :key="idx"
                  class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
                >
                  <div class="flex items-start justify-between gap-3 border-b border-gray-100 bg-gray-50/60 px-4 py-2.5">
                    <div class="flex min-w-0 items-center gap-2">
                      <span class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-700">
                        #{{ item.id ?? '?' }}
                      </span>
                      <span class="truncate text-sm font-medium text-gray-800" :title="item.question">
                        {{ item.question || '（无问题描述）' }}
                      </span>
                    </div>
                    <span
                      class="shrink-0 rounded-md px-2 py-0.5 font-mono text-xs font-bold"
                      :class="(item.similarity ?? 0) >= 0.8 ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
                      :title="`相似度 ${formatSimilarity(item.similarity)}`"
                    >
                      相似度 {{ formatSimilarity(item.similarity) }}
                    </span>
                  </div>
                  <div class="px-4 py-3">
                    <div v-if="item.dataset_name" class="mb-2 flex items-center gap-1.5 text-[11px] text-gray-500">
                      <svg class="h-3.5 w-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                      </svg>
                      {{ item.dataset_name }}
                    </div>
                    <pre class="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-emerald-400">{{ item.sql || '（无 SQL）' }}</pre>
                    <p v-if="item.context_summary" class="mt-2 text-xs text-gray-500 line-clamp-2" :title="item.context_summary">
                      {{ item.context_summary }}
                    </p>
                  </div>
                </div>
              </div>

              <!-- No hit body hint -->
              <div v-if="!testResult.found" class="rounded-lg border border-dashed border-gray-300 bg-white/60 p-6 text-center text-xs text-gray-400">
                可尝试调低「相似度阈值」或切换「检索模式」后重新执行检索。
              </div>
            </div>
          </div>

          <!-- Right Column: Trace Logs -->
          <div v-if="testResult" class="flex w-1/3 flex-col bg-slate-50">
            <div class="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-3">
              <h3 class="flex items-center gap-2 text-sm font-bold text-gray-700">
                <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                </svg>
                执行日志 (Trace)
              </h3>
            </div>
            <div class="flex-1 space-y-2 overflow-y-auto p-4 font-mono text-xs">
              <div
                v-for="(log, i) in testResult.logs"
                :key="i"
                class="break-all rounded border p-2 text-xs shadow-sm"
                :class="
                  isHitLog(log)
                    ? 'bg-blue-50 border-blue-200 text-blue-900'
                    : isNeutralLog(log)
                      ? 'bg-amber-50 border-amber-200 text-amber-900'
                      : 'bg-white border-gray-200 text-gray-600'
                "
              >
                <span class="mr-2 text-gray-300">{{ Number(i) + 1 }}.</span>
                {{ log }}
              </div>
              <div v-if="!testResult.logs || testResult.logs.length === 0" class="mt-10 text-center text-gray-400 italic">
                暂无详细日志
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
